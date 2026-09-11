#include <smarthydro/runtime/edge_runtime.hpp>

#include <algorithm>
#include <array>
#include <cmath>
#include <utility>
#include <vector>

namespace smarthydro {
namespace {

constexpr double kSecondsPerHour = 3600.0;
constexpr double kEventToleranceSeconds = 1.0e-9;
constexpr double kDeliveredTolerance = 1.0e-12;
// Il campione pubblicato dal runtime puo coprire intervalli relativamente
// lunghi (nelle anteprime del dashboard sono 15 minuti), ma l'irrigazione ha
// un fronte di salita molto piu rapido. Durante una richiesta Threshold la
// soglia superiore viene quindi ricontrollata ogni minuto, senza aumentare la
// quantita di telemetria o di punti del grafico.
constexpr double kIrrigationThresholdCheckSeconds = 60.0;

}  // namespace

void EdgeRuntime::stop_all_actuators() noexcept {
    actuators_->stop_all();
    effective_actuator_output_ = {};
}

void EdgeRuntime::reset_actuator_observation() noexcept {
    detector_command_observation_ = {};
    detector_output_observation_ = {};
}

void EdgeRuntime::record_actuator_observation(
    const ActuatorCommand& command,
    const ActuatorOutput& output) noexcept {
    detector_command_observation_.requested_irrigation_volume_liters =
        std::max(
            detector_command_observation_.requested_irrigation_volume_liters,
            command.requested_irrigation_volume_liters);
    detector_command_observation_.lighting_percent = std::max(
        detector_command_observation_.lighting_percent,
        command.lighting_percent);
    detector_output_observation_.water_pump_on =
        detector_output_observation_.water_pump_on || output.water_pump_on;
    detector_output_observation_.water_pump_flow_liters_per_hour =
        std::max(
            detector_output_observation_.water_pump_flow_liters_per_hour,
            output.water_pump_flow_liters_per_hour);
    detector_output_observation_.irrigation_volume_liters_last_step +=
        output.irrigation_volume_liters_last_step;
    detector_output_observation_.water_pump_on_time_seconds_last_step +=
        output.water_pump_on_time_seconds_last_step;
    detector_output_observation_.lighting_power_watts = std::max(
        detector_output_observation_.lighting_power_watts,
        output.lighting_power_watts);
    for (std::size_t index = 0;
         index < kFertilizerTypeCount;
         ++index) {
        detector_command_observation_.fertilizer_valves_open[index] =
            detector_command_observation_.fertilizer_valves_open[index] ||
            command.fertilizer_valves_open[index];
        detector_output_observation_.fertilizer_valves_open[index] =
            detector_output_observation_.fertilizer_valves_open[index] ||
            output.fertilizer_valves_open[index];
        detector_output_observation_
            .fertilizer_flow_milliliters_per_hour[index] = std::max(
                detector_output_observation_
                    .fertilizer_flow_milliliters_per_hour[index],
                output.fertilizer_flow_milliliters_per_hour[index]);
        detector_output_observation_
            .fertilizer_volume_milliliters_last_step[index] +=
                output.fertilizer_volume_milliliters_last_step[index];
    }
}

void EdgeRuntime::apply_safe_fallback(
    double delta_time_seconds,
    EdgeStepResult& result,
    bool replace_decisions) {
    actuators_->stop_all();
    result.operational_state = operational_state_;
    if (replace_decisions) {
        for (std::size_t index = 0;
             index < kControlledVariableCount;
             ++index) {
            auto& decision = result.decisions[index];
            decision.status = ControlDecisionStatus::BLOCKED;
            decision.safety_critical = true;
            decision.fault_severity = ControlFaultSeverity::CRITICAL;
            decision.actuator =
                control_system_.recipe().controllers[index].actuator;
            decision.message =
                "runtime is in " +
                std::string(to_string(operational_state_));
        }
    }
    advance_physics(delta_time_seconds, result);
    update_dose_histories(delta_time_seconds, result);
}

void EdgeRuntime::apply_decisions(
    double delta_time_seconds,
    EdgeStepResult& result) {
    const auto water_index =
        controlled_variable_index(ControlledVariable::SOIL_MOISTURE);
    const auto light_index =
        controlled_variable_index(ControlledVariable::LIGHT);
    const double lighting_percent = std::clamp(
        result.decisions[light_index].command, 0.0, 100.0);
    lighting_.set_command_percent(lighting_percent);

    const double water_command =
        result.decisions[water_index].command;
    if (result.decisions[water_index].status ==
            ControlDecisionStatus::BLOCKED &&
        result.decisions[water_index].safety_critical) {
        water_pump_.cancel();
    } else if (water_command <= 0.0 && water_pump_.active()) {
        // Un comando Threshold inattivo significa che la soglia superiore e'
        // stata raggiunta: non basta evitare una nuova richiesta, va annullato
        // anche il volume residuo di quella precedente.
        water_pump_.cancel();
    } else if (water_command > 0.0 && !water_pump_.active()) {
        water_pump_.request_volume_liters(water_command);
    }

    fertilizer_valves_.close_all();
    FertilizerValues<double> requested_doses{};
    requested_doses[fertilizer_index(FertilizerType::NITROGEN)] =
        std::max(
            0.0,
            result.decisions[controlled_variable_index(
                ControlledVariable::NITROGEN)]
                .command);
    requested_doses[fertilizer_index(FertilizerType::PHOSPHORUS)] =
        std::max(
            0.0,
            result.decisions[controlled_variable_index(
                ControlledVariable::PHOSPHORUS)]
                .command);
    requested_doses[fertilizer_index(FertilizerType::POTASSIUM)] =
        std::max(
            0.0,
            result.decisions[controlled_variable_index(
                ControlledVariable::POTASSIUM)]
                .command);

    const double ph_command =
        result.decisions[controlled_variable_index(
            ControlledVariable::PH)]
            .command;
    if (ph_command > 0.0) {
        requested_doses[fertilizer_index(FertilizerType::PH_UP)] =
            ph_command;
    } else if (ph_command < 0.0) {
        requested_doses[fertilizer_index(FertilizerType::PH_DOWN)] =
            std::abs(ph_command);
    }

    FertilizerValues<double> close_after_seconds{};
    std::vector<double> close_events;
    const bool supervise_threshold_irrigation =
        water_pump_.active() &&
        control_system_.recipe().controllers[water_index].selected_strategy ==
            StrategyType::THRESHOLD;
    if (water_pump_.active()) {
        const double available_pump_seconds = std::min(
            delta_time_seconds,
            water_pump_.remaining_time_seconds());
        for (std::size_t index = 0;
             index < kFertilizerTypeCount;
             ++index) {
            if (requested_doses[index] <= 0.0) {
                continue;
            }
            const auto type = static_cast<FertilizerType>(index);
            const double flow =
                actuators_->config()
                    .fertilizer_flow_milliliters_per_hour[index];
            const double requested_seconds =
                requested_doses[index] / flow * kSecondsPerHour;
            close_after_seconds[index] = std::min(
                requested_seconds, available_pump_seconds);
            if (close_after_seconds[index] > kEventToleranceSeconds) {
                fertilizer_valves_.set_open(type, true);
                close_events.push_back(close_after_seconds[index]);
            }
        }
    }

    if (supervise_threshold_irrigation) {
        // Questi checkpoint condividono la stessa timeline degli eventi di
        // chiusura delle valvole. advance_physics() vede quindi sotto-intervalli
        // da al massimo un minuto mentre il risultato esterno resta un solo
        // campione della durata richiesta dal chiamante.
        for (double checkpoint = kIrrigationThresholdCheckSeconds;
             checkpoint <= delta_time_seconds + kEventToleranceSeconds;
             checkpoint += kIrrigationThresholdCheckSeconds) {
            close_events.push_back(std::min(checkpoint, delta_time_seconds));
        }
    }

    std::sort(close_events.begin(), close_events.end());
    close_events.erase(
        std::unique(
            close_events.begin(),
            close_events.end(),
            [](double left, double right) {
                return std::abs(left - right) <=
                       kEventToleranceSeconds;
            }),
        close_events.end());

    double elapsed = 0.0;
    bool threshold_supervision_finished = false;
    const auto check_irrigation_threshold = [&]() {
        if (!supervise_threshold_irrigation ||
            threshold_supervision_finished) {
            return;
        }

        SensorReadings moisture_reading;
        moisture_reading.timestamp_seconds =
            environment_->state().simulation_time_seconds;
        moisture_reading.soil_moisture_percent =
            sensors_[sensor_channel_index(SensorChannel::SOIL_MOISTURE)]
                ->read(environment_->state());
        fault_injector_.alter_readings(
            moisture_reading,
            environment_->state().simulation_time_seconds);

        auto request = base_request(kIrrigationThresholdCheckSeconds);
        request.controller_input.measured_value =
            moisture_reading.soil_moisture_percent;
        request.source_valid =
            moisture_reading.soil_moisture_percent.has_value();
        const auto decision = control_system_.execute(
            ControlledVariable::SOIL_MOISTURE,
            request);
        if ((decision.status == ControlDecisionStatus::BLOCKED &&
             decision.safety_critical) ||
            decision.command <= 0.0) {
            water_pump_.cancel();
            threshold_supervision_finished = true;
        } else if (!water_pump_.active()) {
            // La richiesta si e' esaurita senza raggiungere la soglia: lo
            // stato isteretico resta attivo e il ciclo esterno potra emettere
            // una nuova dose, ma non servono altre letture rapide in questo
            // stesso campione.
            threshold_supervision_finished = true;
        }
    };

    for (const double event_time : close_events) {
        if (event_time > elapsed + kEventToleranceSeconds) {
            advance_physics(event_time - elapsed, result);
            elapsed = event_time;
            check_irrigation_threshold();
        }
        for (std::size_t index = 0;
             index < kFertilizerTypeCount;
             ++index) {
            if (close_after_seconds[index] > 0.0 &&
                close_after_seconds[index] <=
                    event_time + kEventToleranceSeconds) {
                fertilizer_valves_.set_open(
                    static_cast<FertilizerType>(index), false);
                close_after_seconds[index] = 0.0;
            }
        }
    }

    if (elapsed < delta_time_seconds - kEventToleranceSeconds) {
        advance_physics(delta_time_seconds - elapsed, result);
    }
    fertilizer_valves_.close_all();
}

void EdgeRuntime::advance_physics(
    double delta_time_seconds,
    EdgeStepResult& result) {
    // ActuatorSimulator::step() puo' completare la richiesta corrente
    // (acqua o fertilizzante) esattamente entro questo passo e, in tal
    // caso, azzera lei stessa command().fertilizer_valves_open non appena
    // finisce di erogare (vedi close_fertilizer_valves_preserving_last_step
    // in actuator_simulator.cpp). Se leggessimo command() DOPO step(), un
    // comando perfettamente soddisfatto in un solo ciclo sembrerebbe "mai
    // richiesto" mentre l'uscita mostra comunque il volume erogato: il
    // fault detector lo scambierebbe per un attuatore attivo senza comando
    // (active_without_command, CRITICO) e porterebbe il runtime in
    // EmergencyLockdown, bloccando irrigazione e fertilizzanti per il
    // resto della simulazione. Va quindi catturato PRIMA di step().
    const ActuatorCommand command_before_step = actuators_->command();
    actuators_->step(delta_time_seconds);
    effective_actuator_output_ = fault_injector_.alter_output(
        command_before_step,
        actuators_->output(),
        actuators_->config(),
        delta_time_seconds,
        environment_->state().simulation_time_seconds);
    record_actuator_observation(
        command_before_step, effective_actuator_output_);
    const auto& output = effective_actuator_output_;
    result.delivered_water_liters +=
        output.irrigation_volume_liters_last_step;
    for (std::size_t index = 0;
         index < kFertilizerTypeCount;
         ++index) {
        result.delivered_fertilizer_milliliters[index] +=
            output.fertilizer_volume_milliliters_last_step[index];
    }
    environment_->step(delta_time_seconds, output);
}

void EdgeRuntime::update_dose_histories(
    double delta_time_seconds,
    const EdgeStepResult& result) {
    for (const auto variable : {
             ControlledVariable::PH,
             ControlledVariable::NITROGEN,
             ControlledVariable::PHOSPHORUS,
             ControlledVariable::POTASSIUM}) {
        seconds_since_last_dose_[
            controlled_variable_index(variable)] +=
            delta_time_seconds;
    }

    const std::array<
        std::pair<ControlledVariable, double>,
        4>
        delivered{{
            {
                ControlledVariable::PH,
                result.delivered_fertilizer_milliliters[
                    fertilizer_index(FertilizerType::PH_UP)] +
                    result.delivered_fertilizer_milliliters[
                        fertilizer_index(FertilizerType::PH_DOWN)],
            },
            {
                ControlledVariable::NITROGEN,
                result.delivered_fertilizer_milliliters[
                    fertilizer_index(FertilizerType::NITROGEN)],
            },
            {
                ControlledVariable::PHOSPHORUS,
                result.delivered_fertilizer_milliliters[
                    fertilizer_index(FertilizerType::PHOSPHORUS)],
            },
            {
                ControlledVariable::POTASSIUM,
                result.delivered_fertilizer_milliliters[
                    fertilizer_index(FertilizerType::POTASSIUM)],
            },
        }};

    for (const auto& [variable, dose] : delivered) {
        if (dose <= kDeliveredTolerance) {
            continue;
        }
        const auto index = controlled_variable_index(variable);
        cumulative_phase_dose_milliliters_[index] += dose;
        daily_dose_milliliters_[index] += dose;
        seconds_since_last_dose_[index] = 0.0;
    }
}

}  // namespace smarthydro
