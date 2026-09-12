#include <smarthydro/events/event_bus.hpp>
#include <smarthydro/recipes/recipe_json.hpp>
#include <smarthydro/runtime/edge_runtime.hpp>

#include <gtest/gtest.h>

#include <algorithm>
#include <cmath>
#include <limits>
#include <memory>
#include <string>
#include <utility>
#include <variant>
#include <vector>

namespace {

smarthydro::Recipe load_demo_recipe() {
    return smarthydro::load_recipe_json(
        SMARTHYDRO_EXAMPLE_RECIPE_PATH);
}

smarthydro::SensorConfig deterministic_sensors() {
    smarthydro::SensorConfig config;
    for (auto* channel : {
             &config.temperature,
             &config.air_humidity,
             &config.soil_moisture,
             &config.soil_conductivity,
             &config.ph,
             &config.light_ppfd}) {
        channel->bias = 0.0;
        channel->noise_standard_deviation = 0.0;
        channel->resolution = 0.0;
        channel->dropout_probability = 0.0;
        channel->calibration_correction = 0.0;
    }
    return config;
}

class ConstantSensor final : public smarthydro::ISensor {
public:
    ConstantSensor(
        smarthydro::SensorChannel channel,
        double value)
        : channel_(channel), value_(value) {}

    smarthydro::SensorChannel channel() const noexcept override {
        return channel_;
    }

    std::optional<double> read(
        const smarthydro::EnvironmentState&) override {
        return value_;
    }

private:
    smarthydro::SensorChannel channel_;
    double value_;
};

class RecordingObserver final : public smarthydro::IEventObserver {
public:
    void on_event(
        const smarthydro::EdgeDomainEvent& event) override {
        events.push_back(event);
    }

    std::vector<smarthydro::EdgeDomainEvent> events;
};

class SequenceSensor final : public smarthydro::ISensor {
public:
    SequenceSensor(
        smarthydro::SensorChannel channel,
        std::vector<std::optional<double>> values)
        : channel_(channel), values_(std::move(values)) {
        if (values_.empty()) {
            throw std::invalid_argument(
                "sequence sensor requires at least one value");
        }
    }

    smarthydro::SensorChannel channel() const noexcept override {
        return channel_;
    }

    std::optional<double> read(
        const smarthydro::EnvironmentState&) override {
        const auto value = values_[
            std::min(next_value_, values_.size() - 1)];
        ++next_value_;
        return value;
    }

private:
    smarthydro::SensorChannel channel_;
    std::vector<std::optional<double>> values_;
    std::size_t next_value_ = 0;
};

void set_constant_sensor(
    smarthydro::SensorAdapterArray& sensors,
    smarthydro::SensorChannel channel,
    double value) {
    sensors[smarthydro::sensor_channel_index(channel)] =
        std::make_unique<ConstantSensor>(channel, value);
}

smarthydro::SensorAdapterArray constant_sensor_array() {
    smarthydro::SensorAdapterArray sensors;
    set_constant_sensor(
        sensors, smarthydro::SensorChannel::TEMPERATURE, 22.0);
    set_constant_sensor(
        sensors, smarthydro::SensorChannel::AIR_HUMIDITY, 60.0);
    set_constant_sensor(
        sensors, smarthydro::SensorChannel::SOIL_MOISTURE, 60.0);
    set_constant_sensor(
        sensors,
        smarthydro::SensorChannel::SOIL_CONDUCTIVITY,
        1.8 * std::pow(0.60, 1.30));
    set_constant_sensor(
        sensors, smarthydro::SensorChannel::PH, 6.2);
    set_constant_sensor(
        sensors, smarthydro::SensorChannel::LIGHT, 450.0);
    return sensors;
}

const smarthydro::EdgeEvent* transition_event(
    const smarthydro::EdgeStepResult& result,
    smarthydro::OperationalState destination) {
    for (const auto& event : result.events) {
        if (event.type ==
                smarthydro::EdgeEventType::OPERATIONAL_STATE_CHANGED &&
            event.current_operational_state == destination) {
            return &event;
        }
    }
    return nullptr;
}

TEST(EdgeRuntimeTest, RunsRecipeImmediatelyWithoutManualConfirmation) {
    smarthydro::EdgeRuntime runtime(
        load_demo_recipe(),
        {},
        {},
        deterministic_sensors());

    // Adopting the recipe already confirmed every controller from its
    // selected_strategy — no ConfirmConfiguration command needed before the
    // first control cycle runs.
    const auto result = runtime.step(60.0);

    for (const auto& decision : result.decisions) {
        EXPECT_NE(
            decision.status,
            smarthydro::ControlDecisionStatus::BLOCKED);
    }
}

TEST(EdgeRuntimeTest, ExecutesConfirmedRecipeOnPhysicalSimulators) {
    auto recipe = load_demo_recipe();
    auto& soil_target =
        recipe.phases.front().targets[
            smarthydro::controlled_variable_index(
                smarthydro::ControlledVariable::SOIL_MOISTURE)];
    soil_target.setpoint = 85.0;
    soil_target.allowed_range = {80.0, 90.0};
    soil_target.safety_range = {0.0, 100.0};

    smarthydro::EnvironmentConfig environment_config;
    environment_config.initial_nitrogen_mg_per_liter = 100.0;
    // Deficit di umidita' deliberato rispetto alla banda 80-90 impostata
    // sopra (altrimenti environment_for_recipe() la campionerebbe vicina al
    // setpoint di fase, vedi environment_simulator.hpp, e il comando atteso
    // sotto — 0.5 L, una pompa che reagisce a un vero deficit — non
    // scatterebbe).
    environment_config.initial_soil_moisture_percent = 20.0;
    smarthydro::EdgeRuntime runtime(
        std::move(recipe),
        {},
        environment_config,
        deterministic_sensors());
    runtime.confirm_all_configurations();

    const auto result = runtime.step(900.0);
    const auto water_index =
        smarthydro::controlled_variable_index(
            smarthydro::ControlledVariable::SOIL_MOISTURE);
    const auto nitrogen_index =
        smarthydro::controlled_variable_index(
            smarthydro::ControlledVariable::NITROGEN);
    const double delivered_nitrogen =
        result.delivered_fertilizer_milliliters[
            smarthydro::fertilizer_index(
                smarthydro::FertilizerType::NITROGEN)];

    EXPECT_EQ(
        result.decisions[water_index].status,
        smarthydro::ControlDecisionStatus::APPLIED);
    EXPECT_DOUBLE_EQ(
        result.decisions[water_index].command,
        0.5);
    EXPECT_NE(
        result.decisions[nitrogen_index].status,
        smarthydro::ControlDecisionStatus::BLOCKED);
    EXPECT_GT(
        result.decisions[nitrogen_index].command,
        0.0);
    EXPECT_NEAR(result.delivered_water_liters, 0.5, 1.0e-12);
    EXPECT_NEAR(
        delivered_nitrogen,
        result.decisions[nitrogen_index].command,
        1.0e-12);
    EXPECT_NEAR(
        runtime.cumulative_phase_dose_milliliters(
            smarthydro::ControlledVariable::NITROGEN),
        delivered_nitrogen,
        1.0e-12);
    EXPECT_NEAR(
        runtime.daily_dose_milliliters(
            smarthydro::ControlledVariable::NITROGEN),
        delivered_nitrogen,
        1.0e-12);
    EXPECT_GT(
        result.environment_state.nitrogen_mg_per_liter,
        environment_config.initial_nitrogen_mg_per_liter);
    EXPECT_DOUBLE_EQ(
        result.environment_state.simulation_time_seconds,
        900.0);
}

TEST(EdgeRuntimeTest, EnablesLightOnlyAfterPpfdPeakAndPersistentDusk) {
    auto sensors = constant_sensor_array();
    sensors[smarthydro::sensor_channel_index(
        smarthydro::SensorChannel::LIGHT)] =
        std::make_unique<SequenceSensor>(
            smarthydro::SensorChannel::LIGHT,
            std::vector<std::optional<double>>{
                0.0, 100.0, 400.0, 400.0,
                10.0, 10.0, 10.0, 10.0, 10.0});

    smarthydro::EdgeRuntime runtime(
        load_demo_recipe(),
        std::move(sensors),
        std::make_unique<smarthydro::ActuatorSimulatorAdapter>(),
        std::make_unique<smarthydro::EnvironmentSimulatorAdapter>(
            smarthydro::EnvironmentConfig{}, 23U));
    runtime.confirm_all_configurations();

    // Il picco (400 PPFD) non basta: durante la discesa filtrata il
    // controllore deve aspettare due campioni da 15 minuti sotto la soglia
    // di crepuscolo. Fino al primo campione basso, la lampada non parte.
    for (int step = 0; step < 8; ++step) {
        const auto result = runtime.step(900.0);
        EXPECT_DOUBLE_EQ(result.actuator_output.lighting_power_watts, 0.0);
        EXPECT_DOUBLE_EQ(result.actuator_command.lighting_percent, 0.0);
    }

    const auto dusk_confirmed = runtime.step(900.0);
    EXPECT_GT(dusk_confirmed.actuator_output.lighting_power_watts, 0.0);
    EXPECT_DOUBLE_EQ(
        dusk_confirmed.actuator_command.lighting_percent, 100.0);
}

TEST(EdgeRuntimeTest, SupervisesUpperMoistureThresholdWithinLongStep) {
    auto recipe = load_demo_recipe();
    const auto water_index =
        smarthydro::controlled_variable_index(
            smarthydro::ControlledVariable::SOIL_MOISTURE);
    auto& soil_target = recipe.phases.front().targets[water_index];
    soil_target.setpoint = 67.0;
    soil_target.allowed_range = {66.0, 68.0};
    soil_target.safety_range = {0.0, 100.0};

    smarthydro::EnvironmentConfig environment_config;
    environment_config.initial_soil_moisture_percent = 65.0;
    smarthydro::EdgeRuntime runtime(
        std::move(recipe),
        {},
        environment_config,
        deterministic_sensors());

    const auto result = runtime.step(900.0);

    ASSERT_GT(result.decisions[water_index].command, 0.0);
    // Il comando iniziale richiede 0.5 L, ma il controllo rapido deve
    // interromperlo appena la misura supera il 68%, senza attendere la fine
    // del campione da 15 minuti.
    EXPECT_GT(result.delivered_water_liters, 0.0);
    EXPECT_LT(
        result.delivered_water_liters,
        result.decisions[water_index].command);
    EXPECT_FALSE(result.actuator_output.water_pump_on);
    EXPECT_DOUBLE_EQ(
        result.actuator_output.remaining_irrigation_volume_liters,
        0.0);
    EXPECT_LT(result.environment_state.soil_moisture_percent, 75.0);
}

TEST(EdgeRuntimeTest, NutrientControlRequiresTheResistiveProbeEstimate) {
    auto sensor_config = deterministic_sensors();
    sensor_config.soil_conductivity.dropout_probability = 1.0;
    smarthydro::EdgeRuntime runtime(
        load_demo_recipe(), {}, {}, sensor_config);
    runtime.confirm_all_configurations();

    const auto result = runtime.step(60.0);

    EXPECT_FALSE(result.readings.soil_bulk_ec_ms_cm.has_value());
    EXPECT_FALSE(
        result.readings.fertilizer_concentration_mg_per_liter.has_value());
    for (const auto variable : {
             smarthydro::ControlledVariable::NITROGEN,
             smarthydro::ControlledVariable::PHOSPHORUS,
             smarthydro::ControlledVariable::POTASSIUM}) {
        const auto& decision = result.decisions[
            smarthydro::controlled_variable_index(variable)];
        EXPECT_EQ(
            decision.status,
            smarthydro::ControlDecisionStatus::BLOCKED);
        EXPECT_EQ(
            decision.fault_severity,
            smarthydro::ControlFaultSeverity::RECOVERABLE);
    }
}

TEST(EdgeRuntimeTest, RejectsInvalidStepDuration) {
    smarthydro::EdgeRuntime runtime(
        load_demo_recipe(),
        {},
        {},
        deterministic_sensors());

    EXPECT_THROW(runtime.step(0.0), std::invalid_argument);
    EXPECT_THROW(
        runtime.step(
            std::numeric_limits<double>::quiet_NaN()),
        std::invalid_argument);
}

TEST(EdgeRuntimeTest, ProducesMonotonicSequencesAndStartupEvent) {
    smarthydro::EdgeRuntime runtime(
        load_demo_recipe(),
        {},
        {},
        deterministic_sensors());
    runtime.confirm_all_configurations();

    const auto first = runtime.step(60.0);
    const auto second = runtime.step(60.0);

    EXPECT_EQ(first.sequence_number, 0U);
    EXPECT_EQ(second.sequence_number, 1U);
    EXPECT_EQ(
        first.operational_state,
        smarthydro::OperationalState::NOMINAL);
    EXPECT_EQ(
        runtime.operational_state(),
        smarthydro::OperationalState::NOMINAL);
    ASSERT_EQ(first.events.size(), 1U);
    EXPECT_EQ(
        first.events.front().type,
        smarthydro::EdgeEventType::RUNTIME_STARTED);
    EXPECT_TRUE(second.events.empty());
}

TEST(EdgeRuntimeTest, ReportsRecipePhaseTransition) {
    auto recipe = load_demo_recipe();
    recipe.phases.front().duration_hours = 0.01;
    smarthydro::EdgeRuntime runtime(
        std::move(recipe),
        {},
        {},
        deterministic_sensors());
    runtime.confirm_all_configurations();

    const auto first = runtime.step(60.0);
    const auto second = runtime.step(60.0);

    EXPECT_EQ(first.phase_name, "VegetativeGrowth");
    EXPECT_EQ(second.phase_name, "Flowering");
    ASSERT_EQ(second.events.size(), 1U);
    EXPECT_EQ(
        second.events.front().type,
        smarthydro::EdgeEventType::RECIPE_PHASE_CHANGED);
    EXPECT_NE(
        second.events.front().message.find("Flowering"),
        std::string::npos);
}

TEST(EdgeRuntimeTest, CompletesOnceAndKeepsTheFinalPhaseActive) {
    auto recipe = load_demo_recipe();
    for (auto& phase : recipe.phases) {
        phase.duration_hours = 0.01;
    }
    smarthydro::EdgeRuntime runtime(
        std::move(recipe), {}, {}, deterministic_sensors());
    auto event_bus = std::make_shared<smarthydro::EventBus>();
    auto observer = std::make_shared<RecordingObserver>();
    event_bus->subscribe(observer);
    runtime.attach_event_bus(event_bus, "completion-zone");
    runtime.confirm_all_configurations();

    const auto first = runtime.step(60.0);
    const auto final_phase = runtime.step(60.0);
    const auto completed = runtime.step(60.0);
    const auto maintained = runtime.step(60.0);

    EXPECT_FALSE(first.recipe_completed);
    EXPECT_FALSE(final_phase.recipe_completed);
    EXPECT_TRUE(completed.recipe_completed);
    EXPECT_TRUE(maintained.recipe_completed);
    EXPECT_TRUE(runtime.recipe_completed());
    EXPECT_EQ(completed.phase_name, final_phase.phase_name);
    EXPECT_EQ(maintained.phase_name, final_phase.phase_name);
    ASSERT_EQ(completed.events.size(), 1U);
    EXPECT_EQ(
        completed.events.front().type,
        smarthydro::EdgeEventType::RECIPE_COMPLETED);
    EXPECT_TRUE(maintained.events.empty());
    std::size_t completed_events = 0;
    for (const auto& event : observer->events) {
        if (std::holds_alternative<smarthydro::RecipeCompleted>(event)) {
            ++completed_events;
        }
    }
    EXPECT_EQ(completed_events, 1U);
}

TEST(EdgeRuntimeTest, EscalatesPersistentSensorFailureThroughDegraded) {
    auto recipe = load_demo_recipe();
    recipe.phases.front().photoperiod = {0.0, 24.0};
    auto sensor_config = deterministic_sensors();
    sensor_config.soil_moisture.dropout_probability = 1.0;
    smarthydro::EdgeRuntime runtime(
        std::move(recipe),
        {},
        {},
        sensor_config);
    runtime.confirm_all_configurations();

    const auto first_failure = runtime.step(60.0);

    EXPECT_EQ(
        first_failure.operational_state,
        smarthydro::OperationalState::DEGRADED);
    EXPECT_EQ(
        runtime.operational_state(),
        smarthydro::OperationalState::DEGRADED);
    EXPECT_DOUBLE_EQ(first_failure.delivered_water_liters, 0.0);
    EXPECT_FALSE(first_failure.actuator_output.water_pump_on);
    const auto light_index =
        smarthydro::controlled_variable_index(
            smarthydro::ControlledVariable::LIGHT);
    EXPECT_NE(
        first_failure.decisions[light_index].status,
        smarthydro::ControlDecisionStatus::BLOCKED);
    // La lampada resta spenta a questo primo istante (t~0 nel fotoperiodo
    // 0-24h impostato sopra, quindi sempre "luce naturale"): il ramo LIGHT
    // (control_system.cpp, RecipeControlSystem::execute) non supplisce mai
    // durante la finestra di luce naturale, qualunque sia il deficit —
    // solo DOPO IL TRAMONTO (fuori da quella finestra) puo' accendersi, e
    // solo se il DLI di oggi e' ancora sotto il target. Con l'intera
    // giornata dichiarata "naturale" qui, quel momento non arriva mai: il
    // canale luce resta comunque "non bloccato" (verificato sopra, status
    // APPLIED con un messaggio di vincolo solare), semplicemente non ha
    // motivo di accendersi.
    EXPECT_DOUBLE_EQ(
        first_failure.actuator_output.lighting_power_watts,
        0.0);
    const auto* degraded_event = transition_event(
        first_failure, smarthydro::OperationalState::DEGRADED);
    ASSERT_NE(degraded_event, nullptr);
    EXPECT_EQ(
        degraded_event->previous_operational_state,
        smarthydro::OperationalState::NOMINAL);

    const auto second_failure = runtime.step(60.0);
    EXPECT_EQ(
        second_failure.operational_state,
        smarthydro::OperationalState::DEGRADED);

    const auto third_failure = runtime.step(60.0);
    EXPECT_EQ(
        third_failure.operational_state,
        smarthydro::OperationalState::EMERGENCY_LOCKDOWN);
    EXPECT_EQ(
        runtime.operational_state(),
        smarthydro::OperationalState::EMERGENCY_LOCKDOWN);
    bool lockdown_event_found = false;
    for (const auto& event : third_failure.events) {
        if (event.type ==
            smarthydro::EdgeEventType::EMERGENCY_LOCKDOWN_ENTERED) {
            lockdown_event_found = true;
            EXPECT_NE(
                event.message.find("soil_moisture"),
                std::string::npos);
        }
    }
    EXPECT_TRUE(lockdown_event_found);

    const auto held_step = runtime.step(60.0);

    EXPECT_EQ(
        held_step.operational_state,
        smarthydro::OperationalState::EMERGENCY_LOCKDOWN);
    EXPECT_DOUBLE_EQ(held_step.delivered_water_liters, 0.0);
    EXPECT_FALSE(held_step.actuator_output.water_pump_on);
    for (const auto& decision : held_step.decisions) {
        EXPECT_EQ(
            decision.status,
            smarthydro::ControlDecisionStatus::BLOCKED);
        EXPECT_TRUE(decision.safety_critical);
        EXPECT_EQ(
            decision.message,
            "runtime is in EmergencyLockdown");
    }
    for (const auto& event : held_step.events) {
        EXPECT_NE(
            event.type,
            smarthydro::EdgeEventType::EMERGENCY_LOCKDOWN_ENTERED);
    }
}

TEST(EdgeRuntimeTest, NutrientProcessExcursionDoesNotStopIrrigation) {
    auto recipe = load_demo_recipe();
    const auto nitrogen_index = smarthydro::controlled_variable_index(
        smarthydro::ControlledVariable::NITROGEN);
    auto& target = recipe.phases.front().targets[nitrogen_index];
    target.setpoint = 5.0;
    target.allowed_range = {0.0, 10.0};
    target.safety_range = {0.0, 10.0};

    smarthydro::EnvironmentConfig environment;
    // Diverso dal default, quindi environment_for_recipe() lo conserva:
    // la stima N resta deliberatamente sopra il limite agronomico.
    environment.initial_nitrogen_mg_per_liter = 100.0;
    auto sensor_config = deterministic_sensors();
    smarthydro::EdgeRuntime runtime(
        std::move(recipe), {}, environment, sensor_config);

    for (int cycle = 0; cycle < 5; ++cycle) {
        const auto result = runtime.step(60.0);
        EXPECT_EQ(
            result.operational_state,
            smarthydro::OperationalState::DEGRADED);
        EXPECT_EQ(
            result.decisions[nitrogen_index].status,
            smarthydro::ControlDecisionStatus::BLOCKED);
        const auto water_index = smarthydro::controlled_variable_index(
            smarthydro::ControlledVariable::SOIL_MOISTURE);
        EXPECT_NE(
            result.decisions[water_index].message,
            "runtime is in EmergencyLockdown");
    }
    EXPECT_NE(
        runtime.operational_state(),
        smarthydro::OperationalState::EMERGENCY_LOCKDOWN);
}

TEST(EdgeRuntimeTest, AutomaticallyRecoversFromDegradedAfterHealthyCycles) {
    auto sensors = constant_sensor_array();
    sensors[smarthydro::sensor_channel_index(
        smarthydro::SensorChannel::SOIL_MOISTURE)] =
        std::make_unique<SequenceSensor>(
            smarthydro::SensorChannel::SOIL_MOISTURE,
            std::vector<std::optional<double>>{
                std::nullopt, 60.0, 60.0});
    smarthydro::OperationalStatePolicy policy;
    policy.healthy_steps_before_nominal = 2;

    smarthydro::EdgeRuntime runtime(
        load_demo_recipe(),
        std::move(sensors),
        std::make_unique<smarthydro::ActuatorSimulatorAdapter>(),
        std::make_unique<smarthydro::EnvironmentSimulatorAdapter>(
            smarthydro::EnvironmentConfig{}, 20U),
        policy);
    runtime.confirm_all_configurations();

    const auto failure = runtime.step(60.0);
    const auto first_healthy = runtime.step(60.0);
    const auto recovered = runtime.step(60.0);

    EXPECT_EQ(
        failure.operational_state,
        smarthydro::OperationalState::DEGRADED);
    EXPECT_EQ(
        first_healthy.operational_state,
        smarthydro::OperationalState::DEGRADED);
    EXPECT_EQ(
        recovered.operational_state,
        smarthydro::OperationalState::NOMINAL);
    const auto* recovery_event = transition_event(
        recovered, smarthydro::OperationalState::NOMINAL);
    ASSERT_NE(recovery_event, nullptr);
    EXPECT_EQ(
        recovery_event->previous_operational_state,
        smarthydro::OperationalState::DEGRADED);
}

TEST(EdgeRuntimeTest, RequiresManualResetAfterEmergencyLockdown) {
    auto sensors = constant_sensor_array();
    sensors[smarthydro::sensor_channel_index(
        smarthydro::SensorChannel::SOIL_MOISTURE)] =
        std::make_unique<SequenceSensor>(
            smarthydro::SensorChannel::SOIL_MOISTURE,
            std::vector<std::optional<double>>{
                10.0, 60.0, 60.0, 60.0});
    smarthydro::OperationalStatePolicy policy;
    policy.healthy_steps_before_nominal = 2;

    smarthydro::EdgeRuntime runtime(
        load_demo_recipe(),
        std::move(sensors),
        std::make_unique<smarthydro::ActuatorSimulatorAdapter>(),
        std::make_unique<smarthydro::EnvironmentSimulatorAdapter>(
            smarthydro::EnvironmentConfig{}, 21U),
        policy);
    runtime.confirm_all_configurations();

    EXPECT_FALSE(runtime.request_manual_reset());
    const auto emergency = runtime.step(60.0);
    const auto still_locked = runtime.step(60.0);

    EXPECT_EQ(
        emergency.operational_state,
        smarthydro::OperationalState::EMERGENCY_LOCKDOWN);
    EXPECT_EQ(
        still_locked.operational_state,
        smarthydro::OperationalState::EMERGENCY_LOCKDOWN);
    EXPECT_TRUE(runtime.request_manual_reset());

    const auto reset_verified = runtime.step(60.0);
    const auto first_healthy = runtime.step(60.0);
    const auto recovered = runtime.step(60.0);

    EXPECT_EQ(
        reset_verified.operational_state,
        smarthydro::OperationalState::DEGRADED);
    EXPECT_NE(
        transition_event(
            reset_verified, smarthydro::OperationalState::DEGRADED),
        nullptr);
    EXPECT_EQ(
        first_healthy.operational_state,
        smarthydro::OperationalState::DEGRADED);
    EXPECT_EQ(
        recovered.operational_state,
        smarthydro::OperationalState::NOMINAL);
    EXPECT_FALSE(runtime.request_manual_reset());
}

TEST(EdgeRuntimeTest, StopsAllActuatorsWhenPhysicalCommandFails) {
    auto recipe = load_demo_recipe();
    auto& soil_target =
        recipe.phases.front().targets[
            smarthydro::controlled_variable_index(
                smarthydro::ControlledVariable::SOIL_MOISTURE)];
    soil_target.setpoint = 85.0;
    soil_target.allowed_range = {80.0, 90.0};
    soil_target.safety_range = {0.0, 100.0};

    smarthydro::ActuatorConfig actuator_config;
    actuator_config.maximum_irrigation_volume_liters = 0.1;
    smarthydro::EnvironmentConfig environment_config;
    // Deficit di umidita' deliberato rispetto alla banda 80-90 impostata
    // sopra, cosi' il controllore richiede piu' acqua del tetto minuscolo
    // dell'attuatore (0.1L) e fa scattare il fallimento atteso sotto — senza
    // questo, environment_for_recipe() campionerebbe l'umidita' iniziale
    // vicina al setpoint di fase e nessun comando eccederebbe mai 0.1L.
    environment_config.initial_soil_moisture_percent = 20.0;
    smarthydro::EdgeRuntime runtime(
        std::move(recipe),
        actuator_config,
        environment_config,
        deterministic_sensors());
    runtime.confirm_all_configurations();

    const auto result = runtime.step(60.0);

    EXPECT_EQ(
        result.operational_state,
        smarthydro::OperationalState::EMERGENCY_LOCKDOWN);
    EXPECT_DOUBLE_EQ(result.delivered_water_liters, 0.0);
    EXPECT_FALSE(result.actuator_output.water_pump_on);
    EXPECT_DOUBLE_EQ(result.actuator_output.lighting_power_watts, 0.0);
    for (const bool open : result.actuator_output.fertilizer_valves_open) {
        EXPECT_FALSE(open);
    }

    bool actuator_error_reported = false;
    for (const auto& event : result.events) {
        if (event.type ==
            smarthydro::EdgeEventType::EMERGENCY_LOCKDOWN_ENTERED) {
            actuator_error_reported =
                event.message.find("actuator command failed") !=
                std::string::npos;
        }
    }
    EXPECT_TRUE(actuator_error_reported);
}

TEST(EdgeRuntimeTest, AcceptsInjectedAdapterImplementations) {
    auto sensors = constant_sensor_array();

    smarthydro::EdgeRuntime runtime(
        load_demo_recipe(),
        std::move(sensors),
        std::make_unique<smarthydro::ActuatorSimulatorAdapter>(),
        std::make_unique<smarthydro::EnvironmentSimulatorAdapter>(
            smarthydro::EnvironmentConfig{}, 9U));
    runtime.confirm_all_configurations();

    const auto result = runtime.step(60.0);

    EXPECT_EQ(
        result.operational_state,
        smarthydro::OperationalState::NOMINAL);
    EXPECT_DOUBLE_EQ(*result.readings.temperature_c, 22.0);
    EXPECT_DOUBLE_EQ(*result.readings.air_humidity_percent, 60.0);
    EXPECT_DOUBLE_EQ(*result.readings.soil_moisture_percent, 60.0);
    EXPECT_DOUBLE_EQ(*result.readings.ph, 6.2);
    EXPECT_DOUBLE_EQ(*result.readings.light_ppfd_umol_m2_s, 450.0);
}

TEST(EdgeRuntimeTest, RejectsMissingInjectedAdapters) {
    auto sensors = smarthydro::make_simulated_sensor_adapters(
        deterministic_sensors(), 11U);
    sensors[smarthydro::sensor_channel_index(
        smarthydro::SensorChannel::PH)]
        .reset();

    EXPECT_THROW(
        smarthydro::EdgeRuntime(
            load_demo_recipe(),
            std::move(sensors),
            std::make_unique<smarthydro::ActuatorSimulatorAdapter>(),
            std::make_unique<smarthydro::EnvironmentSimulatorAdapter>(
                smarthydro::EnvironmentConfig{}, 12U)),
        std::invalid_argument);
}

TEST(EdgeRuntimeTest, RejectsInvalidOperationalStatePolicy) {
    auto sensors = constant_sensor_array();
    smarthydro::OperationalStatePolicy invalid_policy;
    invalid_policy.healthy_steps_before_nominal = 0;

    EXPECT_THROW(
        smarthydro::EdgeRuntime(
            load_demo_recipe(),
            std::move(sensors),
            std::make_unique<smarthydro::ActuatorSimulatorAdapter>(),
            std::make_unique<smarthydro::EnvironmentSimulatorAdapter>(
                smarthydro::EnvironmentConfig{}, 22U),
            invalid_policy),
        std::invalid_argument);
}

}  // namespace
