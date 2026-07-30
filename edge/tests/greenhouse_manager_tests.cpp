#include <smarthydro/recipes/recipe_json.hpp>
#include <smarthydro/runtime/greenhouse_manager.hpp>

#include <gtest/gtest.h>

#include <map>
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

smarthydro::PidConfig soil_pid() {
    return {
        60.0,
        0.1,
        0.001,
        0.0,
        {0.0, 1.0},
        smarthydro::ControlDirection::INCREASES_PROCESS_VALUE,
    };
}

class RecordingObserver final : public smarthydro::IEventObserver {
public:
    void on_event(
        const smarthydro::EdgeDomainEvent& event) override {
        events.push_back(event);
    }

    std::vector<smarthydro::EdgeDomainEvent> events;
};

TEST(GreenhouseManagerTest, InactiveZoneHasNoRuntimeAndProducesNoSteps) {
    smarthydro::GreenhouseManager manager;
    auto& zone = manager.add_inactive_zone("zone-1");

    EXPECT_EQ(manager.size(), 1U);
    EXPECT_EQ(
        zone.lifecycle_state(),
        smarthydro::ZoneLifecycleState::IDLE);
    EXPECT_STREQ(
        smarthydro::to_string(zone.lifecycle_state()),
        "Idle");
    EXPECT_FALSE(zone.is_active());
    EXPECT_FALSE(zone.is_running());
    EXPECT_TRUE(zone.cultivation_id().empty());
    EXPECT_TRUE(manager.step_all(60.0).empty());
    EXPECT_THROW(zone.runtime(), std::logic_error);
    EXPECT_THROW(
        manager.step_zone("zone-1", 60.0),
        std::logic_error);
}

TEST(GreenhouseManagerTest, ActivationCreatesAndConfirmsRuntimeOnlyOnce) {
    smarthydro::GreenhouseManager manager;
    auto& zone = manager.add_inactive_zone("zone-1");
    const smarthydro::RuntimeCommandEnvelope activation{
        "activate-1",
        smarthydro::ActivateCultivationCommand{
            "cultivation-1",
            load_demo_recipe(),
        },
    };

    const auto first =
        manager.execute_command("zone-1", activation);
    const auto replay =
        manager.execute_command("zone-1", activation);
    const auto results = manager.step_all(60.0);

    EXPECT_TRUE(first.success());
    EXPECT_FALSE(first.replayed);
    EXPECT_TRUE(replay.success());
    EXPECT_TRUE(replay.replayed);
    EXPECT_TRUE(zone.is_active());
    EXPECT_TRUE(zone.is_running());
    EXPECT_EQ(
        zone.lifecycle_state(),
        smarthydro::ZoneLifecycleState::RUNNING);
    EXPECT_EQ(zone.cultivation_id(), "cultivation-1");
    EXPECT_TRUE(
        zone.runtime()
            .control_system()
            .all_configurations_confirmed());
    EXPECT_EQ(results.size(), 1U);
    EXPECT_EQ(results.at("zone-1").sequence_number, 0U);
}

TEST(GreenhouseManagerTest, InactiveZoneRejectsOperationalCommands) {
    smarthydro::GreenhouseManager manager;
    auto& zone = manager.add_inactive_zone("zone-1");

    const auto result = manager.execute_command(
        "zone-1",
        {
            "stop-before-activation",
            smarthydro::EmergencyStopCommand{"operator stop"},
        });

    EXPECT_FALSE(result.success());
    EXPECT_EQ(
        result.message,
        "zone is inactive; ActivateCultivation is required");
    EXPECT_FALSE(zone.is_active());
}

TEST(GreenhouseManagerTest, ActivationFailureMovesZoneToErrorAndCanRetry) {
    smarthydro::GreenhouseManager manager;
    auto& zone = manager.add_inactive_zone("zone-1");
    auto invalid_recipe = load_demo_recipe();
    invalid_recipe.phases.clear();

    const auto failed = manager.execute_command(
        "zone-1",
        {
            "activate-invalid",
            smarthydro::ActivateCultivationCommand{
                "cultivation-invalid",
                invalid_recipe,
            },
        });

    EXPECT_FALSE(failed.success());
    EXPECT_EQ(
        zone.lifecycle_state(),
        smarthydro::ZoneLifecycleState::ERROR);
    EXPECT_FALSE(zone.is_active());
    EXPECT_FALSE(zone.last_error().empty());
    EXPECT_TRUE(manager.step_all(60.0).empty());

    const auto retried = manager.execute_command(
        "zone-1",
        {
            "activate-valid",
            smarthydro::ActivateCultivationCommand{
                "cultivation-valid",
                load_demo_recipe(),
            },
        });

    EXPECT_TRUE(retried.success());
    EXPECT_EQ(
        zone.lifecycle_state(),
        smarthydro::ZoneLifecycleState::RUNNING);
    EXPECT_TRUE(zone.last_error().empty());
    EXPECT_EQ(zone.cultivation_id(), "cultivation-valid");
}

TEST(GreenhouseManagerTest, PauseStopsActuatorsAndResumeRestartsCycles) {
    smarthydro::GreenhouseManager manager;
    auto& zone = manager.add_inactive_zone("zone-1");
    ASSERT_TRUE(
        manager.execute_command(
            "zone-1",
            {
                "activate-1",
                smarthydro::ActivateCultivationCommand{
                    "cultivation-1",
                    load_demo_recipe(),
                },
            })
            .success());
    manager.step_zone("zone-1", 60.0);
    const auto operational_state_before_pause =
        zone.runtime().operational_state();

    const auto paused = manager.execute_command(
        "zone-1",
        {
            "pause-1",
            smarthydro::PauseCultivationCommand{},
        });

    EXPECT_TRUE(paused.success());
    EXPECT_EQ(
        zone.lifecycle_state(),
        smarthydro::ZoneLifecycleState::PAUSED);
    EXPECT_TRUE(zone.is_active());
    EXPECT_FALSE(zone.is_running());
    EXPECT_EQ(
        zone.runtime().operational_state(),
        operational_state_before_pause);
    EXPECT_FALSE(zone.runtime().actuator_output().water_pump_on);
    EXPECT_DOUBLE_EQ(
        zone.runtime().actuator_output().lighting_power_watts,
        0.0);
    EXPECT_TRUE(manager.step_all(60.0).empty());
    EXPECT_THROW(
        manager.step_zone("zone-1", 60.0),
        std::logic_error);

    const auto resumed = manager.execute_command(
        "zone-1",
        {
            "resume-1",
            smarthydro::ResumeCultivationCommand{},
        });

    EXPECT_TRUE(resumed.success());
    EXPECT_EQ(
        zone.lifecycle_state(),
        smarthydro::ZoneLifecycleState::RUNNING);
    EXPECT_TRUE(zone.is_running());
    EXPECT_EQ(
        zone.runtime().operational_state(),
        operational_state_before_pause);
    EXPECT_EQ(
        manager.step_zone("zone-1", 60.0).sequence_number,
        1U);
}

TEST(GreenhouseManagerTest, StopReleasesRuntimeAndReturnsZoneToIdle) {
    smarthydro::GreenhouseManager manager;
    auto& zone = manager.add_inactive_zone("zone-1");
    ASSERT_TRUE(
        manager.execute_command(
            "zone-1",
            {
                "activate-1",
                smarthydro::ActivateCultivationCommand{
                    "cultivation-1",
                    load_demo_recipe(),
                },
            })
            .success());

    const auto stopped = manager.execute_command(
        "zone-1",
        {
            "stop-1",
            smarthydro::StopCultivationCommand{},
        });

    EXPECT_TRUE(stopped.success());
    EXPECT_EQ(
        zone.lifecycle_state(),
        smarthydro::ZoneLifecycleState::IDLE);
    EXPECT_FALSE(zone.is_active());
    EXPECT_FALSE(zone.is_running());
    EXPECT_TRUE(zone.cultivation_id().empty());
    EXPECT_TRUE(zone.last_error().empty());
    EXPECT_THROW(zone.runtime(), std::logic_error);
}

TEST(GreenhouseManagerTest, LifecycleNamesAreStable) {
    EXPECT_STREQ(
        smarthydro::to_string(
            smarthydro::ZoneLifecycleState::IDLE),
        "Idle");
    EXPECT_STREQ(
        smarthydro::to_string(
            smarthydro::ZoneLifecycleState::STARTING),
        "Starting");
    EXPECT_STREQ(
        smarthydro::to_string(
            smarthydro::ZoneLifecycleState::RUNNING),
        "Running");
    EXPECT_STREQ(
        smarthydro::to_string(
            smarthydro::ZoneLifecycleState::PAUSED),
        "Paused");
    EXPECT_STREQ(
        smarthydro::to_string(
            smarthydro::ZoneLifecycleState::STOPPING),
        "Stopping");
    EXPECT_STREQ(
        smarthydro::to_string(
            smarthydro::ZoneLifecycleState::ERROR),
        "Error");
}

TEST(GreenhouseManagerTest, PublishesEveryLifecycleTransition) {
    auto event_bus = std::make_shared<smarthydro::EventBus>();
    auto observer = std::make_shared<RecordingObserver>();
    event_bus->subscribe(observer);
    smarthydro::GreenhouseManager manager(event_bus);
    manager.add_inactive_zone("zone-1");

    ASSERT_TRUE(
        manager.execute_command(
            "zone-1",
            {
                "activate-1",
                smarthydro::ActivateCultivationCommand{
                    "cultivation-1",
                    load_demo_recipe(),
                },
            })
            .success());
    ASSERT_TRUE(
        manager.execute_command(
            "zone-1",
            {
                "pause-1",
                smarthydro::PauseCultivationCommand{},
            })
            .success());
    ASSERT_TRUE(
        manager.execute_command(
            "zone-1",
            {
                "resume-1",
                smarthydro::ResumeCultivationCommand{},
            })
            .success());
    ASSERT_TRUE(
        manager.execute_command(
            "zone-1",
            {
                "stop-1",
                smarthydro::StopCultivationCommand{},
            })
            .success());

    std::vector<std::string> transitions;
    for (const auto& event : observer->events) {
        if (const auto* lifecycle =
                std::get_if<smarthydro::ZoneLifecycleChanged>(
                    &event)) {
            transitions.push_back(
                lifecycle->previous_state + "->" +
                lifecycle->current_state);
        }
    }
    EXPECT_EQ(
        transitions,
        (std::vector<std::string>{
            "Idle->Starting",
            "Starting->Running",
            "Running->Paused",
            "Paused->Running",
            "Running->Stopping",
            "Stopping->Idle",
        }));
}

TEST(GreenhouseManagerTest, KeepsTimelinesAndSequencesIndependent) {
    smarthydro::GreenhouseManager manager;
    auto& north = manager.add_simulated_zone(
        "department-a/sector-north",
        load_demo_recipe(),
        {},
        {},
        deterministic_sensors(),
        10U,
        11U);
    auto& south = manager.add_simulated_zone(
        "department-a/sector-south",
        load_demo_recipe(),
        {},
        {},
        deterministic_sensors(),
        20U,
        21U);
    north.confirm_all_configurations();
    south.confirm_all_configurations();

    const auto north_first =
        manager.step_zone("department-a/sector-north", 60.0);
    const auto north_second =
        manager.step_zone("department-a/sector-north", 60.0);
    const auto south_first =
        manager.step_zone("department-a/sector-south", 60.0);

    EXPECT_EQ(manager.size(), 2U);
    EXPECT_EQ(north_first.sequence_number, 0U);
    EXPECT_EQ(north_second.sequence_number, 1U);
    EXPECT_EQ(south_first.sequence_number, 0U);
    EXPECT_DOUBLE_EQ(
        north.runtime().environment_state().simulation_time_seconds,
        120.0);
    EXPECT_DOUBLE_EQ(
        south.runtime().environment_state().simulation_time_seconds,
        60.0);
    EXPECT_NE(
        &north.runtime().environment_state(),
        &south.runtime().environment_state());
}

TEST(GreenhouseManagerTest, CriticalFaultInOneZoneDoesNotLockTheOther) {
    smarthydro::GreenhouseManager manager;
    auto& faulty = manager.add_simulated_zone(
        "faulty-zone",
        load_demo_recipe(),
        {},
        {},
        deterministic_sensors());
    auto& healthy = manager.add_simulated_zone(
        "healthy-zone",
        load_demo_recipe(),
        {},
        {},
        deterministic_sensors(),
        30U,
        31U);
    faulty.confirm_all_configurations();
    healthy.confirm_all_configurations();

    const auto injected = manager.execute_command(
        "faulty-zone",
        {
            "inject-zone-fault",
            smarthydro::InjectFaultCommand{
                "pump-overcurrent",
                smarthydro::ControlFaultSeverity::CRITICAL,
                "simulated zone-local overcurrent",
            },
        });
    const auto results = manager.step_all(60.0);

    EXPECT_TRUE(injected.success());
    EXPECT_EQ(
        results.at("faulty-zone").operational_state,
        smarthydro::OperationalState::EMERGENCY_LOCKDOWN);
    EXPECT_EQ(
        results.at("healthy-zone").operational_state,
        smarthydro::OperationalState::NOMINAL);
    EXPECT_TRUE(
        faulty.runtime().has_injected_fault("pump-overcurrent"));
    EXPECT_FALSE(
        healthy.runtime().has_injected_fault("pump-overcurrent"));
}

TEST(GreenhouseManagerTest, StrategyAndPhaseChangesRemainZoneLocal) {
    smarthydro::GreenhouseManager manager;
    auto& changed =
        manager.add_simulated_zone("changed-zone", load_demo_recipe());
    auto& unchanged =
        manager.add_simulated_zone("unchanged-zone", load_demo_recipe());
    const auto unchanged_version =
        unchanged.runtime().control_system().recipe().version;
    const auto unchanged_phase =
        unchanged.runtime().active_phase_name();

    const auto strategy_result = manager.execute_command(
        "changed-zone",
        {
            "strategy-zone-local",
            smarthydro::ChangeStrategyCommand{
                smarthydro::ControlledVariable::SOIL_MOISTURE,
                smarthydro::StrategyType::PID,
                soil_pid(),
            },
        });
    const auto phase_result = manager.execute_command(
        "changed-zone",
        {
            "phase-zone-local",
            smarthydro::AdvanceRecipePhaseCommand{},
        });

    EXPECT_TRUE(strategy_result.success());
    EXPECT_TRUE(phase_result.success());
    EXPECT_EQ(
        changed.runtime()
            .control_system()
            .recipe()
            .controllers[smarthydro::controlled_variable_index(
                smarthydro::ControlledVariable::SOIL_MOISTURE)]
            .selected_strategy,
        smarthydro::StrategyType::PID);
    EXPECT_EQ(
        unchanged.runtime().control_system().recipe().version,
        unchanged_version);
    EXPECT_EQ(unchanged.runtime().active_phase_name(), unchanged_phase);
    EXPECT_NE(
        changed.runtime().active_phase_name(),
        unchanged.runtime().active_phase_name());
}

TEST(GreenhouseManagerTest, IdempotencyCachesAreIndependentPerZone) {
    smarthydro::GreenhouseManager manager;
    manager.add_simulated_zone("zone-a", load_demo_recipe());
    manager.add_simulated_zone("zone-b", load_demo_recipe());
    const smarthydro::RuntimeCommandEnvelope command{
        "shared-command-id",
        smarthydro::AdvanceRecipePhaseCommand{},
    };

    const auto first_a = manager.execute_command("zone-a", command);
    const auto first_b = manager.execute_command("zone-b", command);
    const auto replay_a = manager.execute_command("zone-a", command);

    EXPECT_TRUE(first_a.success());
    EXPECT_TRUE(first_b.success());
    EXPECT_FALSE(first_a.replayed);
    EXPECT_FALSE(first_b.replayed);
    EXPECT_TRUE(replay_a.replayed);
}

TEST(GreenhouseManagerTest, SharedBusKeepsTelemetryTaggedAndSequenced) {
    auto event_bus = std::make_shared<smarthydro::EventBus>();
    auto observer = std::make_shared<RecordingObserver>();
    event_bus->subscribe(observer);
    smarthydro::GreenhouseManager manager(event_bus);
    auto& zone_a =
        manager.add_simulated_zone("zone-a", load_demo_recipe());
    auto& zone_b =
        manager.add_simulated_zone("zone-b", load_demo_recipe());
    zone_a.confirm_all_configurations();
    zone_b.confirm_all_configurations();

    manager.step_all(60.0);
    manager.step_all(60.0);

    std::map<std::string, std::vector<std::uint64_t>> sequences;
    for (const auto& event : observer->events) {
        if (const auto* telemetry =
                std::get_if<smarthydro::TelemetrySample>(&event)) {
            sequences[telemetry->zone_id].push_back(
                telemetry->sequence_number);
        }
    }
    EXPECT_EQ(
        sequences["zone-a"],
        (std::vector<std::uint64_t>{0U, 1U}));
    EXPECT_EQ(
        sequences["zone-b"],
        (std::vector<std::uint64_t>{0U, 1U}));
}

TEST(GreenhouseManagerTest, RejectsDuplicateAndUnknownZones) {
    smarthydro::GreenhouseManager manager;
    manager.add_simulated_zone("zone-a", load_demo_recipe());

    EXPECT_THROW(
        manager.add_simulated_zone("zone-a", load_demo_recipe()),
        std::invalid_argument);
    EXPECT_THROW(
        manager.zone("missing-zone"),
        std::out_of_range);
    EXPECT_EQ(manager.zone_ids(), (std::vector<std::string>{"zone-a"}));
}

}  // namespace
