#include "smarthydro/event_bus.hpp"
#include "smarthydro/recipe_json.hpp"
#include "smarthydro/runtime_commands.hpp"

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
    EXPECT_FALSE(
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
    smarthydro::EdgeRuntime runtime(
        load_demo_recipe(), {}, {}, deterministic_sensors());
    smarthydro::RuntimeCommandProcessor processor(runtime);
    const auto initial_phase = runtime.active_phase_name();

    const auto advanced = processor.execute(
        {
            "phase-1",
            smarthydro::AdvanceRecipePhaseCommand{},
        });
    const auto last_phase = runtime.active_phase_name();
    const auto refused = processor.execute(
        {
            "phase-2",
            smarthydro::AdvanceRecipePhaseCommand{},
        });

    EXPECT_TRUE(advanced.success());
    EXPECT_NE(last_phase, initial_phase);
    EXPECT_FALSE(refused.success());
    EXPECT_EQ(runtime.active_phase_name(), last_phase);
}

TEST(RuntimeCommandProcessorTest, InjectsResetsAndPublishesSyntheticFault) {
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
                "simulated-overheat",
                smarthydro::ControlFaultSeverity::CRITICAL,
                "temperature forced beyond safety limit",
            },
        });
    const auto emergency_step = runtime.step(60.0);
    const auto reset = processor.execute(
        {
            "fault-reset-1",
            smarthydro::ResetFaultCommand{"simulated-overheat"},
        });

    EXPECT_TRUE(injected.success());
    EXPECT_TRUE(reset.success());
    EXPECT_FALSE(runtime.has_injected_fault("simulated-overheat"));
    EXPECT_EQ(
        emergency_step.operational_state,
        smarthydro::OperationalState::EMERGENCY_LOCKDOWN);
    ASSERT_FALSE(observer->events.empty());
    EXPECT_TRUE(std::holds_alternative<smarthydro::FaultDetected>(
        observer->events.front()));
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
    EXPECT_EQ(
        runtime.actuator_output().lighting_power_watts,
        0.0);
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
