#include <smarthydro/events/event_bus.hpp>
#include <smarthydro/recipes/recipe_json.hpp>
#include <smarthydro/runtime/runtime_commands.hpp>

#include <gtest/gtest.h>

#include <memory>
#include <string>
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

class RecordingObserver final : public smarthydro::IEventObserver {
public:
    void on_event(
        const smarthydro::EdgeDomainEvent& event) override {
        events.push_back(event);
    }

    std::vector<smarthydro::EdgeDomainEvent> events;
};

TEST(RuntimeCommandProcessorTest, ChangesStrategyOnlyOnceForDuplicateId) {
    smarthydro::EdgeRuntime runtime(
        load_demo_recipe(), {}, {}, deterministic_sensors());
    smarthydro::RuntimeCommandProcessor processor(runtime);
    const auto previous_version =
        runtime.control_system().recipe().version;
    smarthydro::RuntimeCommandEnvelope command{
        "strategy-1",
        smarthydro::ChangeStrategyCommand{
            smarthydro::ControlledVariable::SOIL_MOISTURE,
            smarthydro::StrategyType::PID,
            smarthydro::PidConfig{
                60.0,
                0.1,
                0.001,
                0.0,
                {0.0, 1.0},
                smarthydro::ControlDirection::INCREASES_PROCESS_VALUE},
        },
    };

    const auto first = processor.execute(command);
    const auto version_after_first =
        runtime.control_system().recipe().version;
    const auto replay = processor.execute(command);

    EXPECT_TRUE(first.success());
    EXPECT_FALSE(first.replayed);
    EXPECT_TRUE(replay.success());
    EXPECT_TRUE(replay.replayed);
    EXPECT_EQ(version_after_first, previous_version + 1);
    EXPECT_EQ(
        runtime.control_system().recipe().version,
        version_after_first);
    EXPECT_EQ(processor.processed_command_count(), 1U);
}

TEST(RuntimeCommandProcessorTest, PublishesStrategyChangeAndHandlesReview) {
    smarthydro::EdgeRuntime runtime(
        load_demo_recipe(), {}, {}, deterministic_sensors());
    auto event_bus = std::make_shared<smarthydro::EventBus>();
    auto observer = std::make_shared<RecordingObserver>();
    event_bus->subscribe(observer);
    runtime.attach_event_bus(event_bus, "command-zone");
    smarthydro::RuntimeCommandProcessor processor(runtime);

    const auto changed = processor.execute(
        {
            "strategy-2",
            smarthydro::ChangeStrategyCommand{
                smarthydro::ControlledVariable::SOIL_MOISTURE,
                smarthydro::StrategyType::PID,
                smarthydro::PidConfig{
                    60.0,
                    0.1,
                    0.001,
                    0.0,
                    {0.0, 1.0},
                    smarthydro::ControlDirection::INCREASES_PROCESS_VALUE},
            },
        });
    const auto confirmed = processor.execute(
        {
            "confirm-1",
            smarthydro::ConfirmConfigurationCommand{
                smarthydro::ControlledVariable::SOIL_MOISTURE},
        });
    const auto rejected = processor.execute(
        {
            "reject-1",
            smarthydro::RejectConfigurationCommand{
                smarthydro::ControlledVariable::LIGHT},
        });

    EXPECT_TRUE(changed.success());
    EXPECT_TRUE(confirmed.success());
    EXPECT_TRUE(rejected.success());
    EXPECT_EQ(
        runtime.control_system()
            .recipe()
            .controllers[smarthydro::controlled_variable_index(
                smarthydro::ControlledVariable::SOIL_MOISTURE)]
            .confirmation_state,
        smarthydro::ConfirmationState::CONFIRMED);
    EXPECT_EQ(
        runtime.control_system()
            .recipe()
            .controllers[smarthydro::controlled_variable_index(
                smarthydro::ControlledVariable::LIGHT)]
            .confirmation_state,
        smarthydro::ConfirmationState::REJECTED);
    ASSERT_EQ(observer->events.size(), 1U);
    EXPECT_TRUE(std::holds_alternative<smarthydro::StrategyChanged>(
        observer->events.front()));
}

TEST(RuntimeCommandProcessorTest, LoadsNewRecipeAndRestartsItsTimeline) {
    smarthydro::EdgeRuntime runtime(
        load_demo_recipe(), {}, {}, deterministic_sensors());
    runtime.confirm_all_configurations();
    runtime.step(60.0);
    auto replacement = load_demo_recipe();
    ++replacement.version;
    replacement.id = "replacement-recipe";
    replacement.phases.front().name = "ReplacementStart";
    smarthydro::RuntimeCommandProcessor processor(runtime);

    const auto loaded = processor.execute(
        {
            "recipe-1",
            smarthydro::LoadRecipeCommand{replacement},
        });

    EXPECT_TRUE(loaded.success());
    EXPECT_EQ(
        runtime.control_system().recipe().id,
        "replacement-recipe");
    EXPECT_DOUBLE_EQ(runtime.elapsed_recipe_hours(), 0.0);
    EXPECT_EQ(runtime.active_phase_name(), "ReplacementStart");
    // Loading a new recipe (active_recipe_id changing) confirms every
    // controller from its selected_strategy immediately — no separate
    // ConfirmConfiguration command is needed after LoadRecipe succeeds.
    EXPECT_TRUE(
        runtime.control_system().all_configurations_confirmed());
}

TEST(RuntimeCommandProcessorTest, RejectsInvalidRecipeAndReplaysRejection) {
    smarthydro::EdgeRuntime runtime(
        load_demo_recipe(), {}, {}, deterministic_sensors());
    smarthydro::RuntimeCommandProcessor processor(runtime);
    const smarthydro::RuntimeCommandEnvelope command{
        "recipe-invalid",
        smarthydro::LoadRecipeCommand{load_demo_recipe()},
    };

    const auto first = processor.execute(command);
    const auto replay = processor.execute(command);

    EXPECT_FALSE(first.success());
    EXPECT_EQ(
        first.status,
        smarthydro::RuntimeCommandStatus::REJECTED);
    EXPECT_FALSE(first.replayed);
    EXPECT_FALSE(replay.success());
    EXPECT_TRUE(replay.replayed);
    EXPECT_EQ(replay.message, first.message);
}

TEST(RuntimeCommandProcessorTest, AdvancesRecipePhaseUntilLastPhase) {
    auto recipe = load_demo_recipe();
    recipe.phases[0].name = "Avvio e attecchimento";
    recipe.phases[1].name = "Crescita vegetativa";
    auto production = recipe.phases[1];
    production.name = "Fioritura e produzione";
    auto maturation = production;
    maturation.name = "Maturazione";
    recipe.phases.push_back(std::move(production));
    recipe.phases.push_back(std::move(maturation));
    smarthydro::EdgeRuntime runtime(
        std::move(recipe), {}, {}, deterministic_sensors());
    auto event_bus = std::make_shared<smarthydro::EventBus>();
    auto observer = std::make_shared<RecordingObserver>();
    event_bus->subscribe(observer);
    runtime.attach_event_bus(event_bus, "phase-command-zone");
    smarthydro::RuntimeCommandProcessor processor(runtime);
    const auto initial_phase = runtime.active_phase_name();

    const auto first_advance = processor.execute(
        {"phase-1", smarthydro::AdvanceRecipePhaseCommand{}});
    const auto second_advance = processor.execute(
        {"phase-2", smarthydro::AdvanceRecipePhaseCommand{}});
    const auto third_advance = processor.execute(
        {"phase-3", smarthydro::AdvanceRecipePhaseCommand{}});
    const auto last_phase = runtime.active_phase_name();
    const auto refused = processor.execute(
        {"phase-4", smarthydro::AdvanceRecipePhaseCommand{}});
    std::size_t phase_changed_events = 0;
    for (const auto& event : observer->events) {
        if (std::holds_alternative<smarthydro::RecipePhaseChanged>(event)) {
            ++phase_changed_events;
        }
    }

    EXPECT_TRUE(first_advance.success());
    EXPECT_TRUE(second_advance.success());
    EXPECT_TRUE(third_advance.success());
    EXPECT_NE(last_phase, initial_phase);
    EXPECT_EQ(last_phase, "Maturazione");
    EXPECT_FALSE(refused.success());
    EXPECT_EQ(runtime.active_phase_name(), last_phase);
    EXPECT_FALSE(runtime.recipe_completed());
    EXPECT_EQ(phase_changed_events, 3U);
}

TEST(RuntimeCommandProcessorTest, InjectsDetectsResetsAndPublishesTypedFault) {
    smarthydro::EdgeRuntime runtime(
        load_demo_recipe(), {}, {}, deterministic_sensors());
    runtime.confirm_all_configurations();
    auto event_bus = std::make_shared<smarthydro::EventBus>();
    auto observer = std::make_shared<RecordingObserver>();
    event_bus->subscribe(observer);
    runtime.attach_event_bus(event_bus, "fault-zone");
    smarthydro::RuntimeCommandProcessor processor(runtime);

    const auto injected = processor.execute(
        {
            "fault-inject-1",
            smarthydro::InjectFaultCommand{
                smarthydro::FaultSpecification{
                    "simulated-ph-dropout",
                    smarthydro::FaultTargetKind::SENSOR,
                    "ph",
                    smarthydro::FaultMode::SENSOR_DROPOUT,
                    std::nullopt,
                    std::nullopt,
                },
            },
        });
    const auto emergency_step = runtime.step(60.0);
    runtime.step(60.0);
    const auto escalated_step = runtime.step(60.0);
    const auto reset = processor.execute(
        {
            "fault-reset-1",
            smarthydro::ResetFaultCommand{"simulated-ph-dropout"},
        });

    EXPECT_TRUE(injected.success());
    EXPECT_TRUE(reset.success());
    EXPECT_FALSE(runtime.has_injected_fault("simulated-ph-dropout"));
    EXPECT_EQ(
        emergency_step.operational_state,
        smarthydro::OperationalState::DEGRADED);
    EXPECT_EQ(
        escalated_step.operational_state,
        smarthydro::OperationalState::EMERGENCY_LOCKDOWN);
    const smarthydro::FaultDetected* detected_fault = nullptr;
    for (const auto& event : observer->events) {
        if (const auto* fault =
                std::get_if<smarthydro::FaultDetected>(&event)) {
            detected_fault = fault;
            break;
        }
    }
    ASSERT_NE(detected_fault, nullptr);
    EXPECT_EQ(detected_fault->component, "ph_sensor");
    EXPECT_EQ(detected_fault->rule, "missing_value");
    EXPECT_EQ(
        detected_fault->severity,
        smarthydro::ControlFaultSeverity::RECOVERABLE);
}

TEST(RuntimeCommandProcessorTest, TimedSensorOffsetExpiresAndRecovers) {
    smarthydro::OperationalStatePolicy policy;
    policy.recoverable_faults_before_lockdown = 5;
    policy.healthy_steps_before_nominal = 1;
    smarthydro::FaultDetectorConfig detector_config;
    detector_config.values[smarthydro::observed_value_index(
        smarthydro::ObservedValue::PH)].maximum_rate_per_second = 0.001;
    smarthydro::EdgeRuntime runtime(
        load_demo_recipe(),
        {},
        {},
        deterministic_sensors(),
        1U,
        2U,
        policy,
        detector_config);
    runtime.confirm_all_configurations();
    smarthydro::RuntimeCommandProcessor processor(runtime);
    runtime.step(60.0);

    const auto injected = processor.execute(
        {
            "offset-inject",
            smarthydro::InjectFaultCommand{
                smarthydro::FaultSpecification{
                    "temporary-ph-offset",
                    smarthydro::FaultTargetKind::SENSOR,
                    "ph",
                    smarthydro::FaultMode::SENSOR_OFFSET,
                    0.1,
                    60.0,
                },
            },
        });
    const auto affected = runtime.step(60.0);
    const auto returning_to_baseline = runtime.step(60.0);
    const auto recovered = runtime.step(60.0);

    EXPECT_TRUE(injected.success());
    EXPECT_EQ(
        affected.operational_state,
        smarthydro::OperationalState::DEGRADED);
    const auto ph_index =
        smarthydro::controlled_variable_index(
            smarthydro::ControlledVariable::PH);
    const auto soil_index =
        smarthydro::controlled_variable_index(
            smarthydro::ControlledVariable::SOIL_MOISTURE);
    EXPECT_EQ(
        affected.decisions[ph_index].status,
        smarthydro::ControlDecisionStatus::BLOCKED);
    EXPECT_DOUBLE_EQ(affected.decisions[ph_index].command, 0.0);
    EXPECT_NE(
        affected.decisions[ph_index].message.find(
            "maximum_rate_exceeded"),
        std::string::npos);
    EXPECT_NE(
        affected.decisions[soil_index].status,
        smarthydro::ControlDecisionStatus::BLOCKED);
    EXPECT_EQ(
        returning_to_baseline.operational_state,
        smarthydro::OperationalState::DEGRADED);
    EXPECT_EQ(
        recovered.operational_state,
        smarthydro::OperationalState::NOMINAL);
    EXPECT_FALSE(runtime.has_injected_fault("temporary-ph-offset"));
}

TEST(RuntimeCommandProcessorTest, StuckSensorNeedsRepeatedObservation) {
    smarthydro::OperationalStatePolicy policy;
    policy.recoverable_faults_before_lockdown = 5;
    smarthydro::FaultDetectorConfig detector_config;
    detector_config.frozen_cycles = 2;
    for (auto& value_policy : detector_config.values) {
        value_policy.detect_frozen = false;
    }
    detector_config.values[smarthydro::observed_value_index(
        smarthydro::ObservedValue::SOIL_MOISTURE)].detect_frozen = true;
    smarthydro::EdgeRuntime runtime(
        load_demo_recipe(),
        {},
        {},
        deterministic_sensors(),
        1U,
        2U,
        policy,
        detector_config);
    runtime.confirm_all_configurations();
    smarthydro::RuntimeCommandProcessor processor(runtime);

    ASSERT_TRUE(
        processor
            .execute(
                {
                    "stuck-inject",
                    smarthydro::InjectFaultCommand{
                        smarthydro::FaultSpecification{
                            "soil-sensor-stuck",
                            smarthydro::FaultTargetKind::SENSOR,
                            "soil_moisture",
                            smarthydro::FaultMode::SENSOR_STUCK,
                            std::nullopt,
                            std::nullopt,
                        },
                    },
                })
            .success());

    const auto first = runtime.step(60.0);
    const auto second = runtime.step(60.0);

    EXPECT_EQ(
        first.operational_state,
        smarthydro::OperationalState::NOMINAL);
    EXPECT_EQ(
        second.operational_state,
        smarthydro::OperationalState::DEGRADED);
    ASSERT_TRUE(first.readings.soil_moisture_percent.has_value());
    ASSERT_TRUE(second.readings.soil_moisture_percent.has_value());
    EXPECT_DOUBLE_EQ(
        *first.readings.soil_moisture_percent,
        *second.readings.soil_moisture_percent);
}

TEST(RuntimeCommandProcessorTest, DegradedIsolatesSharedPumpOnly) {
    auto recipe = load_demo_recipe();
    // Il test dimostra che il canale luce resta VIVO (decisione non
    // bloccata) nonostante il guasto sulla pompa isoli le altre variabili.
    // Non richiede la luce fisicamente accesa: il nuovo rilevatore PPFD
    // deve, anzi, mantenerla spenta finche' non ha confermato il crepuscolo.
    auto& soil_target =
        recipe.phases.front().targets[
            smarthydro::controlled_variable_index(
                smarthydro::ControlledVariable::SOIL_MOISTURE)];
    soil_target.setpoint = 85.0;
    soil_target.allowed_range = {80.0, 90.0};
    soil_target.safety_range = {0.0, 100.0};

    smarthydro::EnvironmentConfig environment_config;
    // Deficit di umidita' deliberato rispetto alla banda 80-90 sopra, cosi'
    // il pump-fault iniettato sotto ha davvero un comando da bloccare
    // (altrimenti environment_for_recipe() campionerebbe l'umidita'
    // iniziale vicina al setpoint e la pompa non riceverebbe mai un
    // comando diverso da zero da "bloccare").
    environment_config.initial_soil_moisture_percent = 20.0;
    smarthydro::EdgeRuntime runtime(
        std::move(recipe), {}, environment_config, deterministic_sensors());
    runtime.confirm_all_configurations();
    smarthydro::RuntimeCommandProcessor processor(runtime);

    ASSERT_TRUE(
        processor
            .execute(
                {
                    "pump-fault-inject",
                    smarthydro::InjectFaultCommand{
                        smarthydro::FaultSpecification{
                            "pump-stuck-off",
                            smarthydro::FaultTargetKind::ACTUATOR,
                            "water_pump",
                            smarthydro::FaultMode::ACTUATOR_STUCK_OFF,
                            std::nullopt,
                            std::nullopt,
                        },
                    },
                })
            .success());

    const auto detected = runtime.step(60.0);
    const auto isolated = runtime.step(60.0);

    EXPECT_EQ(
        detected.operational_state,
        smarthydro::OperationalState::DEGRADED);
    EXPECT_EQ(
        isolated.operational_state,
        smarthydro::OperationalState::DEGRADED);
    for (const auto variable : {
             smarthydro::ControlledVariable::SOIL_MOISTURE,
             smarthydro::ControlledVariable::PH,
             smarthydro::ControlledVariable::NITROGEN,
             smarthydro::ControlledVariable::PHOSPHORUS,
             smarthydro::ControlledVariable::POTASSIUM}) {
        const auto& decision =
            isolated.decisions[
                smarthydro::controlled_variable_index(variable)];
        EXPECT_EQ(
            decision.status,
            smarthydro::ControlDecisionStatus::BLOCKED);
        EXPECT_DOUBLE_EQ(decision.command, 0.0);
        EXPECT_NE(
            decision.message.find("commanded_without_response"),
            std::string::npos);
    }
    const auto& light_decision =
        isolated.decisions[
            smarthydro::controlled_variable_index(
                smarthydro::ControlledVariable::LIGHT)];
    EXPECT_NE(
        light_decision.status,
        smarthydro::ControlDecisionStatus::BLOCKED);
    EXPECT_DOUBLE_EQ(isolated.actuator_output.lighting_power_watts, 0.0);
    EXPECT_DOUBLE_EQ(isolated.delivered_water_liters, 0.0);
    EXPECT_DOUBLE_EQ(
        isolated.actuator_command.requested_irrigation_volume_liters,
        0.0);
}

TEST(RuntimeCommandProcessorTest, StuckOnActuatorTriggersImmediateEmergency) {
    auto recipe = load_demo_recipe();
    // Fotoperiodo esteso a tutta la giornata: il ramo LIGHT comanda quindi
    // sempre 0 (fase di luce naturale — vedi control_system.cpp), cosi'
    // il guasto ACTUATOR_STUCK_ON iniettato sotto crea un vero
    // disallineamento comando/uscita (comandato spento, fisicamente
    // acceso) invece di confondersi con un'accensione supplementare
    // notturna gia' legittima di suo.
    recipe.phases.front().photoperiod = {0.0, 24.0};
    smarthydro::EdgeRuntime runtime(
        std::move(recipe), {}, {}, deterministic_sensors());
    runtime.confirm_all_configurations();
    smarthydro::RuntimeCommandProcessor processor(runtime);

    ASSERT_TRUE(
        processor
            .execute(
                {
                    "lighting-fault-inject",
                    smarthydro::InjectFaultCommand{
                        smarthydro::FaultSpecification{
                            "lighting-stuck-on",
                            smarthydro::FaultTargetKind::ACTUATOR,
                            "lighting",
                            smarthydro::FaultMode::ACTUATOR_STUCK_ON,
                            std::nullopt,
                            std::nullopt,
                        },
                    },
                })
            .success());

    const auto result = runtime.step(60.0);

    EXPECT_EQ(
        result.operational_state,
        smarthydro::OperationalState::EMERGENCY_LOCKDOWN);
    EXPECT_GT(result.actuator_output.lighting_power_watts, 0.0);
    EXPECT_DOUBLE_EQ(result.actuator_command.lighting_percent, 0.0);
}

TEST(RuntimeCommandProcessorTest, ManualResetRequiresHealthyActuatorVerification) {
    smarthydro::OperationalStatePolicy policy;
    policy.healthy_steps_before_nominal = 1;
    auto recipe = load_demo_recipe();
    // Fotoperiodo esteso a tutta la giornata: come in
    // StuckOnActuatorTriggersImmediateEmergency, serve un comando LIGHT
    // naturalmente a 0 (fase di luce naturale) perche' il guasto
    // ACTUATOR_STUCK_ON iniettato sotto sia un vero disallineamento
    // comando/uscita, non un'accensione supplementare notturna gia'
    // legittima di suo.
    recipe.phases.front().photoperiod = {0.0, 24.0};
    smarthydro::EdgeRuntime runtime(
        std::move(recipe),
        {},
        {},
        deterministic_sensors(),
        1U,
        2U,
        policy);
    runtime.confirm_all_configurations();
    smarthydro::RuntimeCommandProcessor processor(runtime);
    ASSERT_TRUE(processor.execute({
        "stuck-on-before-reset",
        smarthydro::InjectFaultCommand{{
            "lighting-stuck-during-reset",
            smarthydro::FaultTargetKind::ACTUATOR,
            "lighting",
            smarthydro::FaultMode::ACTUATOR_STUCK_ON,
            std::nullopt,
            std::nullopt,
        }},
    }).success());

    const auto emergency = runtime.step(60.0);
    ASSERT_TRUE(runtime.request_manual_reset());
    const auto unhealthy_verification = runtime.step(60.0);
    ASSERT_TRUE(runtime.reset_injected_fault(
        "lighting-stuck-during-reset"));
    const auto healthy_verification = runtime.step(60.0);
    const auto recovered = runtime.step(60.0);

    EXPECT_EQ(
        emergency.operational_state,
        smarthydro::OperationalState::EMERGENCY_LOCKDOWN);
    EXPECT_EQ(
        unhealthy_verification.operational_state,
        smarthydro::OperationalState::EMERGENCY_LOCKDOWN);
    EXPECT_EQ(
        healthy_verification.operational_state,
        smarthydro::OperationalState::DEGRADED);
    EXPECT_EQ(
        recovered.operational_state,
        smarthydro::OperationalState::NOMINAL);
}

TEST(RuntimeCommandProcessorTest, PersistentLowActuatorResponseEscalates) {
    auto recipe = load_demo_recipe();
    auto& soil_target = recipe.phases.front().targets[
        smarthydro::controlled_variable_index(
            smarthydro::ControlledVariable::SOIL_MOISTURE)];
    soil_target.setpoint = 85.0;
    soil_target.allowed_range = {80.0, 90.0};
    soil_target.safety_range = {0.0, 100.0};
    smarthydro::OperationalStatePolicy state_policy;
    state_policy.recoverable_faults_before_lockdown = 2;
    smarthydro::FaultDetectorConfig detector_config;
    detector_config.actuator_low_response_cycles = 1;
    smarthydro::EnvironmentConfig environment_config;
    // Deficit di umidita' deliberato rispetto alla banda 80-90 sopra, cosi'
    // la pompa riceve davvero un comando la cui risposta lenta iniettata
    // sotto puo' far scattare l'escalation attesa (altrimenti
    // environment_for_recipe() campionerebbe l'umidita' iniziale vicina al
    // setpoint e la pompa non avrebbe mai nulla da fare).
    environment_config.initial_soil_moisture_percent = 20.0;
    smarthydro::EdgeRuntime runtime(
        std::move(recipe),
        {},
        environment_config,
        deterministic_sensors(),
        1U,
        2U,
        state_policy,
        detector_config);
    runtime.confirm_all_configurations();
    smarthydro::RuntimeCommandProcessor processor(runtime);
    ASSERT_TRUE(processor.execute({
        "slow-pump-injection",
        smarthydro::InjectFaultCommand{{
            "slow-water-pump",
            smarthydro::FaultTargetKind::ACTUATOR,
            "water_pump",
            smarthydro::FaultMode::ACTUATOR_SLOW_RESPONSE,
            0.2,
            std::nullopt,
        }},
    }).success());

    const auto degraded = runtime.step(60.0);
    const auto emergency = runtime.step(60.0);

    EXPECT_EQ(
        degraded.operational_state,
        smarthydro::OperationalState::DEGRADED);
    EXPECT_EQ(
        emergency.operational_state,
        smarthydro::OperationalState::EMERGENCY_LOCKDOWN);
}

TEST(RuntimeCommandProcessorTest, RejectsInvalidTypedFault) {
    smarthydro::EdgeRuntime runtime(
        load_demo_recipe(), {}, {}, deterministic_sensors());
    smarthydro::RuntimeCommandProcessor processor(runtime);

    const auto invalid = processor.execute(
        {
            "invalid-fault",
            smarthydro::InjectFaultCommand{
                smarthydro::FaultSpecification{
                    "invalid-slow-factor",
                    smarthydro::FaultTargetKind::ACTUATOR,
                    "water_pump",
                    smarthydro::FaultMode::ACTUATOR_SLOW_RESPONSE,
                    1.5,
                    std::nullopt,
                },
            },
        });

    EXPECT_FALSE(invalid.success());
    EXPECT_FALSE(runtime.has_injected_fault("invalid-slow-factor"));
}

TEST(RuntimeCommandProcessorTest, EmergencyStopIsImmediateAndResetIsControlled) {
    smarthydro::OperationalStatePolicy policy;
    policy.healthy_steps_before_nominal = 1;
    smarthydro::EdgeRuntime runtime(
        load_demo_recipe(),
        {},
        {},
        deterministic_sensors(),
        1U,
        2U,
        policy);
    runtime.confirm_all_configurations();
    smarthydro::RuntimeCommandProcessor processor(runtime);

    const auto stopped = processor.execute(
        {
            "emergency-1",
            smarthydro::EmergencyStopCommand{"operator stop"},
        });
    const auto reset = processor.execute(
        {
            "emergency-reset-1",
            smarthydro::ResetEmergencyCommand{},
        });
    const auto verification = runtime.step(60.0);
    const auto recovered = runtime.step(60.0);

    EXPECT_TRUE(stopped.success());
    // Il test verifica la macchina a stati (emergenza immediata, poi
    // degradato, poi nominale), non un valore di luce specifico. All'ora
    // iniziale 0:00 il ramo LIGHT ora mantiene la lampada spenta: e' la
    // notte prima dell'alba, quindi il contributo solare della giornata
    // non e' ancora completato.
    EXPECT_TRUE(reset.success());
    EXPECT_EQ(
        verification.operational_state,
        smarthydro::OperationalState::DEGRADED);
    EXPECT_EQ(
        recovered.operational_state,
        smarthydro::OperationalState::NOMINAL);
}

TEST(RuntimeCommandProcessorTest, RejectsEmptyIdentifiersAndInvalidOperations) {
    smarthydro::EdgeRuntime runtime(
        load_demo_recipe(), {}, {}, deterministic_sensors());
    smarthydro::RuntimeCommandProcessor processor(runtime);

    const auto missing_id = processor.execute(
        {
            "",
            smarthydro::EmergencyStopCommand{"operator stop"},
        });
    const auto reset_missing_fault = processor.execute(
        {
            "fault-reset-missing",
            smarthydro::ResetFaultCommand{"absent"},
        });
    const auto reset_outside_emergency = processor.execute(
        {
            "emergency-reset-invalid",
            smarthydro::ResetEmergencyCommand{},
        });

    EXPECT_FALSE(missing_id.success());
    EXPECT_FALSE(reset_missing_fault.success());
    EXPECT_FALSE(reset_outside_emergency.success());
    EXPECT_EQ(processor.processed_command_count(), 2U);
}

}  // namespace
