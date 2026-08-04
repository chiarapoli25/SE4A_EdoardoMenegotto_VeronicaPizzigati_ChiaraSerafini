#include <smarthydro/events/event_bus.hpp>
#include <smarthydro/recipes/recipe_json.hpp>
#include <smarthydro/runtime/edge_runtime.hpp>

#include <gtest/gtest.h>

#include <memory>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

namespace {

class RecordingObserver final : public smarthydro::IEventObserver {
public:
    void on_event(
        const smarthydro::EdgeDomainEvent& event) override {
        events.push_back(event);
    }

    std::vector<smarthydro::EdgeDomainEvent> events;
};

class ThrowingObserver final : public smarthydro::IEventObserver {
public:
    void on_event(
        const smarthydro::EdgeDomainEvent&) override {
        throw std::runtime_error("observer failure");
    }
};

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

smarthydro::Recipe load_demo_recipe() {
    return smarthydro::load_recipe_json(
        SMARTHYDRO_EXAMPLE_RECIPE_PATH);
}

template <typename Event>
std::size_t count_events(
    const std::vector<smarthydro::EdgeDomainEvent>& events) {
    std::size_t count = 0;
    for (const auto& event : events) {
        if (std::holds_alternative<Event>(event)) {
            ++count;
        }
    }
    return count;
}

TEST(EventBusTest, PublishesUnsubscribesAndIsolatesObserverFailures) {
    smarthydro::EventBus event_bus;
    auto recorder = std::make_shared<RecordingObserver>();
    auto throwing = std::make_shared<ThrowingObserver>();
    const auto recorder_subscription =
        event_bus.subscribe(recorder);
    event_bus.subscribe(throwing);

    EXPECT_NO_THROW(
        event_bus.publish(
            smarthydro::StateChanged{
                "zone-a",
                60.0,
                smarthydro::OperationalState::NOMINAL,
                smarthydro::OperationalState::DEGRADED,
                "sensor unavailable",
            }));
    ASSERT_EQ(recorder->events.size(), 1U);
    EXPECT_STREQ(
        smarthydro::event_type_name(recorder->events.front()),
        "StateChanged");

    event_bus.unsubscribe(recorder_subscription);
    event_bus.publish(
        smarthydro::EmergencyTriggered{
            "zone-a", 120.0, "persistent failure"});
    EXPECT_EQ(recorder->events.size(), 1U);
}

TEST(EventBusTest, ConsoleAndCsvLoggersRenderTelemetry) {
    smarthydro::TelemetrySample telemetry;
    telemetry.zone_id = "zone-csv";
    telemetry.sequence_number = 7;
    telemetry.timestamp_seconds = 90.0;
    telemetry.readings.soil_moisture_percent = 55.5;
    const smarthydro::EdgeDomainEvent event = telemetry;

    std::ostringstream console_output;
    smarthydro::ConsoleLogger console(console_output);
    console.on_event(event);
    EXPECT_NE(
        console_output.str().find("TelemetrySample"),
        std::string::npos);
    EXPECT_NE(
        console_output.str().find("zone-csv"),
        std::string::npos);

    std::ostringstream csv_output;
    smarthydro::CsvLogger csv(csv_output);
    csv.on_event(event);
    EXPECT_NE(
        csv_output.str().find("timestamp,event_type"),
        std::string::npos);
    EXPECT_NE(
        csv_output.str().find("55.5"),
        std::string::npos);
}

TEST(EventBusTest, LoggersRenderTemporalEvents) {
    const smarthydro::EdgeDomainEvent speed =
        smarthydro::SimulationSpeedChanged{
            "zone-time",
            300.0,
            1.0,
            10.0,
        };
    const smarthydro::EdgeDomainEvent lag =
        smarthydro::SchedulerLagStateChanged{
            "zone-time",
            900.0,
            true,
            1800.0,
            2,
            10.0,
        };
    const smarthydro::EdgeDomainEvent duration =
        smarthydro::SimulationDurationChanged{
            "zone-time",
            300.0,
            true,
            3600.0,
            3900.0,
        };
    const smarthydro::EdgeDomainEvent completed =
        smarthydro::SimulationDurationCompleted{
            "zone-time",
            3900.0,
            3600.0,
        };
    const smarthydro::EdgeDomainEvent recipe_completed =
        smarthydro::RecipeCompleted{
            "zone-time",
            5000.0,
            "recipe-test",
            "Maturazione",
            1200.0,
        };

    EXPECT_STREQ(
        smarthydro::event_type_name(speed),
        "SimulationSpeedChanged");
    EXPECT_STREQ(
        smarthydro::event_type_name(lag),
        "SchedulerLagStateChanged");
    EXPECT_STREQ(
        smarthydro::event_type_name(duration),
        "SimulationDurationChanged");
    EXPECT_STREQ(
        smarthydro::event_type_name(completed),
        "SimulationDurationCompleted");
    EXPECT_STREQ(
        smarthydro::event_type_name(recipe_completed),
        "RecipeCompleted");

    std::ostringstream console_output;
    smarthydro::ConsoleLogger console(console_output);
    console.on_event(speed);
    console.on_event(duration);
    console.on_event(completed);
    console.on_event(recipe_completed);
    console.on_event(lag);
    EXPECT_NE(
        console_output.str().find("1.000000x -> 10.000000x"),
        std::string::npos);
    EXPECT_NE(
        console_output.str().find("pending_steps=2"),
        std::string::npos);
    EXPECT_NE(
        console_output.str().find("duration=3600.000000s"),
        std::string::npos);
    EXPECT_NE(
        console_output.str().find("completed duration=3600.000000s"),
        std::string::npos);
    EXPECT_NE(
        console_output.str().find("recipe-test completed in Maturazione"),
        std::string::npos);

    std::ostringstream csv_output;
    smarthydro::CsvLogger csv(csv_output);
    csv.on_event(speed);
    csv.on_event(duration);
    csv.on_event(completed);
    csv.on_event(recipe_completed);
    csv.on_event(lag);
    EXPECT_NE(
        csv_output.str().find("SimulationSpeedChanged"),
        std::string::npos);
    EXPECT_NE(
        csv_output.str().find("SchedulerLagStateChanged"),
        std::string::npos);
    EXPECT_NE(
        csv_output.str().find("SimulationDurationChanged"),
        std::string::npos);
    EXPECT_NE(
        csv_output.str().find("SimulationDurationCompleted"),
        std::string::npos);
    EXPECT_NE(
        csv_output.str().find("RecipeCompleted"),
        std::string::npos);
}

TEST(EventBusTest, BackendClientReportsUnavailableTransport) {
    smarthydro::EventBus event_bus;
    auto recorder = std::make_shared<RecordingObserver>();
    auto backend = std::make_shared<smarthydro::BackendClient>(
        event_bus,
        "http://127.0.0.1:8000",
        [](const smarthydro::EdgeDomainEvent&) {
            return false;
        });
    event_bus.subscribe(recorder);
    event_bus.subscribe(backend);

    event_bus.publish(
        smarthydro::TelemetrySample{
            "zone-backend",
            1,
            30.0,
        });

    EXPECT_EQ(
        count_events<smarthydro::TelemetrySample>(
            recorder->events),
        1U);
    EXPECT_EQ(
        count_events<smarthydro::BackendUnavailable>(
            recorder->events),
        1U);
}

TEST(EventBusTest, RuntimePublishesTelemetryAndExecutedCommands) {
    auto event_bus = std::make_shared<smarthydro::EventBus>();
    auto recorder = std::make_shared<RecordingObserver>();
    event_bus->subscribe(recorder);
    smarthydro::EdgeRuntime runtime(
        load_demo_recipe(),
        {},
        {},
        deterministic_sensors());
    runtime.attach_event_bus(event_bus, "greenhouse-1");
    runtime.confirm_all_configurations();

    runtime.step(60.0);

    EXPECT_EQ(
        count_events<smarthydro::TelemetrySample>(
            recorder->events),
        1U);
    EXPECT_EQ(
        count_events<smarthydro::CommandExecuted>(
            recorder->events),
        1U);
    for (const auto& event : recorder->events) {
        std::visit(
            [](const auto& value) {
                EXPECT_EQ(value.zone_id, "greenhouse-1");
            },
            event);
    }
}

TEST(EventBusTest, RuntimePublishesStateAndEmergencyEvents) {
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

    auto event_bus = std::make_shared<smarthydro::EventBus>();
    auto recorder = std::make_shared<RecordingObserver>();
    event_bus->subscribe(recorder);
    smarthydro::EdgeRuntime runtime(
        std::move(recipe),
        actuator_config,
        {},
        deterministic_sensors());
    runtime.attach_event_bus(event_bus, "greenhouse-fault");
    runtime.confirm_all_configurations();

    runtime.step(60.0);

    EXPECT_EQ(
        count_events<smarthydro::CommandFailed>(
            recorder->events),
        1U);
    EXPECT_EQ(
        count_events<smarthydro::StateChanged>(
            recorder->events),
        1U);
    EXPECT_EQ(
        count_events<smarthydro::EmergencyTriggered>(
            recorder->events),
        1U);
    EXPECT_EQ(
        count_events<smarthydro::TelemetrySample>(
            recorder->events),
        1U);
}

TEST(EventBusTest, RuntimePublishesAutomaticallyDetectedSensorFault) {
    auto sensor_config = deterministic_sensors();
    sensor_config.temperature.dropout_probability = 1.0;
    auto event_bus = std::make_shared<smarthydro::EventBus>();
    auto recorder = std::make_shared<RecordingObserver>();
    event_bus->subscribe(recorder);
    smarthydro::EdgeRuntime runtime(
        load_demo_recipe(), {}, {}, sensor_config);
    runtime.attach_event_bus(event_bus, "automatic-detector-zone");
    runtime.confirm_all_configurations();

    const auto result = runtime.step(60.0);

    EXPECT_EQ(
        result.operational_state,
        smarthydro::OperationalState::DEGRADED);
    EXPECT_EQ(
        count_events<smarthydro::FaultDetected>(recorder->events),
        1U);
    EXPECT_EQ(
        count_events<smarthydro::StateChanged>(recorder->events),
        1U);
}

}  // namespace
