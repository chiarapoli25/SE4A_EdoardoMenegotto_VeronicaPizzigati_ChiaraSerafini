#include <smarthydro/recipes/recipe_json.hpp>
#include <smarthydro/runtime/simulation_scheduler.hpp>

#include <gtest/gtest.h>

#include <chrono>
#include <cmath>
#include <limits>
#include <memory>
#include <numeric>
#include <string>
#include <vector>

namespace {

using Clock = smarthydro::SimulationScheduler::Clock;
using TimePoint = smarthydro::SimulationScheduler::TimePoint;

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

TimePoint at_seconds(double seconds) {
    return TimePoint{} +
           std::chrono::duration_cast<Clock::duration>(
               std::chrono::duration<double>(seconds));
}

class RecordingObserver final : public smarthydro::IEventObserver {
public:
    void on_event(
        const smarthydro::EdgeDomainEvent& event) override {
        events.push_back(event);
    }

    std::vector<smarthydro::EdgeDomainEvent> events;
};

template <typename Event>
std::vector<Event> events_of_type(
    const std::vector<smarthydro::EdgeDomainEvent>& events) {
    std::vector<Event> selected;
    for (const auto& event : events) {
        if (const auto* value = std::get_if<Event>(&event)) {
            selected.push_back(*value);
        }
    }
    return selected;
}

smarthydro::ZoneController& add_running_zone(
    smarthydro::GreenhouseManager& manager,
    const std::string& zone_id,
    std::uint32_t seed = 10U) {
    auto& zone = manager.add_simulated_zone(
        zone_id,
        load_demo_recipe(),
        {},
        {},
        deterministic_sensors(),
        seed,
        seed + 1U);
    zone.confirm_all_configurations();
    return zone;
}

TEST(SimulationSchedulerTest, RejectsInvalidConfiguration) {
    smarthydro::GreenhouseManager manager;

    EXPECT_THROW(
        smarthydro::SimulationScheduler(
            manager,
            {0.0, 8}),
        std::invalid_argument);
    EXPECT_THROW(
        smarthydro::SimulationScheduler(
            manager,
            {std::numeric_limits<double>::infinity(), 8}),
        std::invalid_argument);
    EXPECT_THROW(
        smarthydro::SimulationScheduler(
            manager,
            {900.0, 0}),
        std::invalid_argument);
}

TEST(SimulationSchedulerTest, OneXWaitsForOneRealQuantum) {
    smarthydro::GreenhouseManager manager;
    auto& zone = add_running_zone(manager, "zone-1");
    smarthydro::SimulationScheduler scheduler(
        manager,
        {900.0, 8});
    scheduler.synchronize(at_seconds(0.0));

    scheduler.accrue(at_seconds(899.0));
    EXPECT_TRUE(scheduler.run_due_steps().empty());
    EXPECT_DOUBLE_EQ(
        zone.runtime().environment_state().simulation_time_seconds,
        0.0);

    scheduler.accrue(at_seconds(900.0));
    const auto results = scheduler.run_due_steps();

    ASSERT_EQ(results.at("zone-1").size(), 1U);
    EXPECT_DOUBLE_EQ(
        results.at("zone-1").front().duration_seconds,
        900.0);
    EXPECT_DOUBLE_EQ(
        zone.runtime().environment_state().simulation_time_seconds,
        900.0);
}

TEST(SimulationSchedulerTest, TenXAndSixtyXScaleRealDeadlines) {
    smarthydro::GreenhouseManager manager;
    auto& ten_x = add_running_zone(manager, "ten-x", 10U);
    auto& sixty_x = add_running_zone(manager, "sixty-x", 20U);
    ten_x.set_time_scale(10.0);
    sixty_x.set_time_scale(60.0);
    smarthydro::SimulationScheduler scheduler(
        manager,
        {900.0, 8});
    scheduler.synchronize(at_seconds(0.0));

    scheduler.accrue(at_seconds(15.0));
    const auto first = scheduler.run_due_steps();
    EXPECT_EQ(first.count("ten-x"), 0U);
    ASSERT_EQ(first.at("sixty-x").size(), 1U);

    scheduler.accrue(at_seconds(90.0));
    const auto second = scheduler.run_due_steps();
    ASSERT_EQ(second.at("ten-x").size(), 1U);
    ASSERT_EQ(second.at("sixty-x").size(), 5U);
}

TEST(SimulationSchedulerTest, PauseDoesNotAccumulateAndPreservesRemainder) {
    smarthydro::GreenhouseManager manager;
    auto& zone = add_running_zone(manager, "zone-1");
    smarthydro::SimulationScheduler scheduler(
        manager,
        {100.0, 8});
    scheduler.synchronize(at_seconds(0.0));
    scheduler.accrue(at_seconds(40.0));
    ASSERT_TRUE(
        manager.execute_command(
            "zone-1",
            {"pause", smarthydro::PauseCultivationCommand{}})
            .success());
    scheduler.synchronize(at_seconds(40.0));

    scheduler.accrue(at_seconds(1040.0));
    EXPECT_TRUE(scheduler.run_due_steps().empty());
    EXPECT_DOUBLE_EQ(
        scheduler.accumulated_simulation_seconds("zone-1"),
        40.0);

    ASSERT_TRUE(
        manager.execute_command(
            "zone-1",
            {"resume", smarthydro::ResumeCultivationCommand{}})
            .success());
    scheduler.synchronize(at_seconds(1040.0));
    scheduler.accrue(at_seconds(1100.0));
    const auto results = scheduler.run_due_steps();

    ASSERT_EQ(results.at("zone-1").size(), 1U);
    EXPECT_DOUBLE_EQ(
        zone.runtime().environment_state().simulation_time_seconds,
        100.0);
}

TEST(SimulationSchedulerTest, StopAndNewActivationResetTemporalState) {
    smarthydro::GreenhouseManager manager;
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
    smarthydro::SimulationScheduler scheduler(
        manager,
        {100.0, 8});
    scheduler.synchronize(at_seconds(0.0));
    scheduler.accrue(at_seconds(150.0));
    scheduler.run_due_steps();
    EXPECT_DOUBLE_EQ(
        scheduler.accumulated_simulation_seconds("zone-1"),
        50.0);

    ASSERT_TRUE(
        manager.execute_command(
            "zone-1",
            {"stop-1", smarthydro::StopCultivationCommand{}})
            .success());
    scheduler.synchronize(at_seconds(150.0));
    EXPECT_DOUBLE_EQ(
        scheduler.accumulated_simulation_seconds("zone-1"),
        0.0);

    ASSERT_TRUE(
        manager.execute_command(
            "zone-1",
            {
                "activate-2",
                smarthydro::ActivateCultivationCommand{
                    "cultivation-2",
                    load_demo_recipe(),
                },
            })
            .success());
    scheduler.synchronize(at_seconds(150.0));
    EXPECT_DOUBLE_EQ(
        scheduler.accumulated_simulation_seconds("zone-1"),
        0.0);
    EXPECT_DOUBLE_EQ(manager.zone("zone-1").time_scale(), 1.0);
    EXPECT_DOUBLE_EQ(
        manager.zone("zone-1")
            .runtime()
            .environment_state()
            .simulation_time_seconds,
        0.0);
}

TEST(SimulationSchedulerTest, SpeedChangeIsNotAppliedRetroactively) {
    smarthydro::GreenhouseManager manager;
    auto& zone = add_running_zone(manager, "zone-1");
    smarthydro::SimulationScheduler scheduler(
        manager,
        {900.0, 8});
    scheduler.synchronize(at_seconds(0.0));
    scheduler.accrue(at_seconds(450.0));

    ASSERT_TRUE(
        manager.execute_command(
            "zone-1",
            {
                "speed-10",
                smarthydro::SetSimulationSpeedCommand{10.0},
            })
            .success());
    scheduler.synchronize(at_seconds(450.0));
    scheduler.accrue(at_seconds(495.0));
    const auto results = scheduler.run_due_steps();

    ASSERT_EQ(results.at("zone-1").size(), 1U);
    EXPECT_DOUBLE_EQ(
        zone.runtime().environment_state().simulation_time_seconds,
        900.0);
}

TEST(SimulationSchedulerTest, DurationCompletesExactlyWithFinalPartialStep) {
    auto event_bus = std::make_shared<smarthydro::EventBus>();
    auto observer = std::make_shared<RecordingObserver>();
    event_bus->subscribe(observer);
    smarthydro::GreenhouseManager manager(event_bus);
    auto& zone = add_running_zone(manager, "zone-1");
    ASSERT_TRUE(
        manager.execute_command(
            "zone-1",
            {
                "duration-1000",
                smarthydro::SetSimulationDurationCommand{1000.0},
            })
            .success());
    smarthydro::SimulationScheduler scheduler(
        manager,
        {900.0, 8});
    scheduler.synchronize(at_seconds(0.0));

    scheduler.accrue(at_seconds(999.0));
    const auto first = scheduler.run_due_steps();
    ASSERT_EQ(first.at("zone-1").size(), 1U);
    EXPECT_DOUBLE_EQ(
        first.at("zone-1").front().duration_seconds,
        900.0);
    EXPECT_TRUE(zone.is_running());
    EXPECT_DOUBLE_EQ(
        *zone.remaining_simulation_seconds(),
        100.0);

    scheduler.accrue(at_seconds(1000.0));
    const auto completed = scheduler.run_due_steps();
    ASSERT_EQ(completed.at("zone-1").size(), 1U);
    EXPECT_DOUBLE_EQ(
        completed.at("zone-1").front().duration_seconds,
        100.0);
    EXPECT_EQ(
        zone.lifecycle_state(),
        smarthydro::ZoneLifecycleState::PAUSED);
    EXPECT_DOUBLE_EQ(
        zone.runtime().environment_state().simulation_time_seconds,
        1000.0);
    EXPECT_DOUBLE_EQ(
        scheduler.accumulated_simulation_seconds("zone-1"),
        0.0);

    const auto completion_events =
        events_of_type<smarthydro::SimulationDurationCompleted>(
            observer->events);
    ASSERT_EQ(completion_events.size(), 1U);
    EXPECT_DOUBLE_EQ(
        completion_events[0].timestamp_seconds,
        1000.0);
    EXPECT_DOUBLE_EQ(
        completion_events[0].duration_seconds,
        1000.0);

    const auto premature_resume = manager.execute_command(
        "zone-1",
        {"resume-before-reset",
         smarthydro::ResumeCultivationCommand{}});
    EXPECT_FALSE(premature_resume.success());
    EXPECT_EQ(
        premature_resume.message,
        "simulation duration is complete; set a new duration or clear the limit");

    ASSERT_TRUE(
        manager.execute_command(
            "zone-1",
            {
                "new-duration",
                smarthydro::SetSimulationDurationCommand{900.0},
            })
            .success());
    EXPECT_TRUE(
        manager.execute_command(
            "zone-1",
            {"resume-new-duration",
             smarthydro::ResumeCultivationCommand{}})
            .success());
}

TEST(SimulationSchedulerTest, DurationAndTimeScaleComposePerZone) {
    smarthydro::GreenhouseManager manager;
    auto& zone = add_running_zone(manager, "zone-1");
    zone.set_time_scale(10.0);
    zone.set_simulation_duration(900.0);
    smarthydro::SimulationScheduler scheduler(
        manager,
        {900.0, 8});
    scheduler.synchronize(at_seconds(0.0));

    scheduler.accrue(at_seconds(89.0));
    EXPECT_TRUE(scheduler.run_due_steps().empty());
    scheduler.accrue(at_seconds(90.0));
    const auto completed = scheduler.run_due_steps();

    ASSERT_EQ(completed.at("zone-1").size(), 1U);
    EXPECT_DOUBLE_EQ(
        completed.at("zone-1").front().duration_seconds,
        900.0);
    EXPECT_EQ(
        zone.lifecycle_state(),
        smarthydro::ZoneLifecycleState::PAUSED);
    EXPECT_DOUBLE_EQ(
        zone.runtime().environment_state().simulation_time_seconds,
        900.0);
}

TEST(SimulationSchedulerTest, DurationBacklogRespectsBudgetAndThenCompletes) {
    auto event_bus = std::make_shared<smarthydro::EventBus>();
    auto observer = std::make_shared<RecordingObserver>();
    event_bus->subscribe(observer);
    smarthydro::GreenhouseManager manager(event_bus);
    auto& zone = add_running_zone(manager, "zone-1");
    zone.set_simulation_duration(1900.0);
    smarthydro::SimulationScheduler scheduler(
        manager,
        {900.0, 2});
    scheduler.synchronize(at_seconds(0.0));
    scheduler.accrue(at_seconds(5000.0));

    const auto first = scheduler.run_due_steps();
    ASSERT_EQ(first.at("zone-1").size(), 2U);
    EXPECT_TRUE(zone.is_running());
    EXPECT_EQ(scheduler.pending_step_count("zone-1"), 1U);
    auto lag_events =
        events_of_type<smarthydro::SchedulerLagStateChanged>(
            observer->events);
    ASSERT_EQ(lag_events.size(), 1U);
    EXPECT_TRUE(lag_events[0].lagging);
    EXPECT_EQ(lag_events[0].pending_steps, 1U);
    EXPECT_DOUBLE_EQ(
        lag_events[0].pending_simulation_seconds,
        100.0);

    const auto completed = scheduler.run_due_steps();
    ASSERT_EQ(completed.at("zone-1").size(), 1U);
    EXPECT_DOUBLE_EQ(
        completed.at("zone-1").front().duration_seconds,
        100.0);
    EXPECT_EQ(
        zone.lifecycle_state(),
        smarthydro::ZoneLifecycleState::PAUSED);
    EXPECT_DOUBLE_EQ(
        zone.runtime().environment_state().simulation_time_seconds,
        1900.0);
    lag_events =
        events_of_type<smarthydro::SchedulerLagStateChanged>(
            observer->events);
    ASSERT_EQ(lag_events.size(), 2U);
    EXPECT_FALSE(lag_events[1].lagging);
}

TEST(SimulationSchedulerTest, GlobalBudgetIsRoundRobinAndLagIsReportedOnce) {
    auto event_bus = std::make_shared<smarthydro::EventBus>();
    auto observer = std::make_shared<RecordingObserver>();
    event_bus->subscribe(observer);
    smarthydro::GreenhouseManager manager(event_bus);
    add_running_zone(manager, "zone-a", 10U);
    add_running_zone(manager, "zone-b", 20U);
    smarthydro::SimulationScheduler scheduler(
        manager,
        {10.0, 8});
    scheduler.synchronize(at_seconds(0.0));
    scheduler.accrue(at_seconds(100.0));

    const auto first = scheduler.run_due_steps();
    ASSERT_EQ(first.at("zone-a").size(), 4U);
    ASSERT_EQ(first.at("zone-b").size(), 4U);
    EXPECT_EQ(scheduler.pending_step_count("zone-a"), 6U);
    EXPECT_EQ(scheduler.pending_step_count("zone-b"), 6U);
    auto lag_events =
        events_of_type<smarthydro::SchedulerLagStateChanged>(
            observer->events);
    ASSERT_EQ(lag_events.size(), 2U);
    EXPECT_TRUE(lag_events[0].lagging);
    EXPECT_TRUE(lag_events[1].lagging);

    const auto second = scheduler.run_due_steps();
    ASSERT_EQ(second.at("zone-a").size(), 4U);
    ASSERT_EQ(second.at("zone-b").size(), 4U);
    lag_events =
        events_of_type<smarthydro::SchedulerLagStateChanged>(
            observer->events);
    EXPECT_EQ(lag_events.size(), 2U);

    const auto third = scheduler.run_due_steps();
    ASSERT_EQ(third.at("zone-a").size(), 2U);
    ASSERT_EQ(third.at("zone-b").size(), 2U);
    EXPECT_EQ(scheduler.pending_step_count("zone-a"), 0U);
    EXPECT_EQ(scheduler.pending_step_count("zone-b"), 0U);
    lag_events =
        events_of_type<smarthydro::SchedulerLagStateChanged>(
            observer->events);
    ASSERT_EQ(lag_events.size(), 4U);
    EXPECT_FALSE(lag_events[2].lagging);
    EXPECT_FALSE(lag_events[3].lagging);
}

TEST(SimulationSchedulerTest, RejectsBackwardsClock) {
    smarthydro::GreenhouseManager manager;
    add_running_zone(manager, "zone-1");
    smarthydro::SimulationScheduler scheduler(manager);
    scheduler.synchronize(at_seconds(10.0));

    EXPECT_THROW(
        scheduler.accrue(at_seconds(9.0)),
        std::logic_error);
}

}  // namespace
