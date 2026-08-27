#include <smarthydro/backend/http_backend_client.hpp>

#include <gtest/gtest.h>
#include <nlohmann/json.hpp>

#include <algorithm>
#include <chrono>
#include <filesystem>
#include <fstream>
#include <mutex>
#include <string>
#include <thread>
#include <utility>
#include <vector>

namespace {

class RecordingTransport final : public smarthydro::IHttpTransport {
public:
    smarthydro::HttpResponse get(const std::string& path) override {
        std::lock_guard<std::mutex> lock(mutex_);
        get_paths.push_back(path);
        if (path.rfind("/api/v1/edges/", 0) == 0) {
            return zone_assignment_response;
        }
        if (path.rfind("/api/v1/recipes/", 0) == 0) {
            return {200, recipe_response};
        }
        return {200, command_response};
    }

    smarthydro::HttpResponse post(
        const std::string& path,
        const std::string& body) override {
        std::lock_guard<std::mutex> lock(mutex_);
        posts.emplace_back(path, body);
        return post_response;
    }

    std::size_t post_count() const {
        std::lock_guard<std::mutex> lock(mutex_);
        return posts.size();
    }

    std::vector<std::pair<std::string, std::string>> post_snapshot() const {
        std::lock_guard<std::mutex> lock(mutex_);
        return posts;
    }

    smarthydro::HttpResponse post_response{201, "{}"};
    std::string command_response = "[]";
    smarthydro::HttpResponse zone_assignment_response{
        503,
        "zone manifest not configured",
    };
    std::string recipe_response = "{}";
    std::vector<std::string> get_paths;

private:
    mutable std::mutex mutex_;
    std::vector<std::pair<std::string, std::string>> posts;
};

std::filesystem::path temporary_outbox(const std::string& test_name) {
    return std::filesystem::temp_directory_path() /
           ("smarthydro-" + test_name + "-" +
            std::to_string(
                std::chrono::steady_clock::now()
                    .time_since_epoch()
                    .count()));
}

template <typename Predicate>
bool wait_until(Predicate predicate) {
    for (int attempt = 0; attempt < 100; ++attempt) {
        if (predicate()) {
            return true;
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(10));
    }
    return false;
}

smarthydro::TelemetrySample telemetry_event() {
    smarthydro::TelemetrySample event;
    event.zone_id = "zone-1";
    event.sequence_number = 7;
    event.timestamp_seconds = 60.0;
    event.readings.temperature_c = 22.5;
    event.readings.air_humidity_percent = 60.0;
    event.readings.soil_moisture_percent = 55.0;
    event.readings.soil_bulk_ec_ms_cm = 0.83;
    event.readings.soil_ec_ms_cm = 1.8;
    event.readings.fertilizer_concentration_mg_per_liter = 400.0;
    event.readings.nitrogen_estimate_mg_per_liter = 150.0;
    event.readings.phosphorus_estimate_mg_per_liter = 50.0;
    event.readings.potassium_estimate_mg_per_liter = 200.0;
    event.readings.ph = 6.2;
    event.readings.light_ppfd_umol_m2_s = 450.0;
    event.active_recipe_id = "recipe-tomato";
    event.active_recipe_version = 4;
    event.current_phase = "Crescita vegetativa";
    event.lifecycle_state = "Running";
    event.time_scale = 10.0;
    event.current_strategies.fill(
        smarthydro::StrategyType::THRESHOLD);
    event.current_strategies[
        smarthydro::controlled_variable_index(
            smarthydro::ControlledVariable::PH)] =
        smarthydro::StrategyType::PID;
    event.current_setpoints = {55.0, 500.0, 6.2, 150.0, 50.0, 200.0};
    event.actuator_command.lighting_percent = 25.0;
    event.actuator_output.lighting_power_watts = 50.0;
    return event;
}

TEST(HttpBackendClientTest, SerializesTelemetryAndActuatorsAsynchronously) {
    smarthydro::EventBus bus;
    const auto outbox = temporary_outbox("success");
    auto transport = std::make_shared<RecordingTransport>();
    smarthydro::HttpBackendConfig config;
    config.boot_id = "boot-test";
    config.edge_id = "edge-test";
    config.outbox_directory = outbox;
    config.command_poll_interval = std::chrono::hours(1);
    auto client = std::make_shared<smarthydro::HttpBackendClient>(
        bus,
        std::vector<std::string>{"zone-1"},
        config,
        transport);
    bus.subscribe(client);
    client->start();

    bus.publish(telemetry_event());

    ASSERT_TRUE(wait_until([&] { return transport->post_count() >= 2; }));
    client->stop();
    const auto posts = transport->post_snapshot();
    ASSERT_EQ(posts.size(), 2U);
    EXPECT_EQ(posts[0].first, "/api/v1/zones/zone-1/telemetry");
    EXPECT_EQ(posts[1].first, "/api/v1/zones/zone-1/actuators");
    const auto telemetry = nlohmann::json::parse(posts[0].second);
    EXPECT_EQ(telemetry.at("boot_id"), "boot-test");
    EXPECT_EQ(telemetry.at("sequence_number"), 7);
    EXPECT_EQ(telemetry.at("soil_ec_ms_cm"), 1.8);
    EXPECT_EQ(
        telemetry.at("fertilizer_concentration_mg_per_liter"),
        400.0);
    EXPECT_EQ(telemetry.at("active_recipe_id"), "recipe-tomato");
    EXPECT_EQ(telemetry.at("active_recipe_version"), 4);
    EXPECT_EQ(telemetry.at("current_phase"), "Crescita vegetativa");
    EXPECT_EQ(telemetry.at("operational_state"), "Nominal");
    EXPECT_EQ(telemetry.at("lifecycle_state"), "Running");
    EXPECT_EQ(telemetry.at("current_strategies").at("ph"), "PID");
    EXPECT_EQ(telemetry.at("current_setpoints").at("nitrogen"), 150.0);
    EXPECT_EQ(telemetry.at("time_scale"), 10.0);
    const auto actuators = nlohmann::json::parse(posts[1].second);
    EXPECT_EQ(actuators.at("command").at("lighting_percent"), 25.0);
    EXPECT_FALSE(std::filesystem::exists(outbox / "unused.json"));
    std::filesystem::remove_all(outbox);
}

TEST(HttpBackendClientTest, PersistsFailedUploadsInOutbox) {
    smarthydro::EventBus bus;
    const auto outbox = temporary_outbox("failure");
    auto transport = std::make_shared<RecordingTransport>();
    transport->post_response = {503, "offline"};
    smarthydro::HttpBackendConfig config;
    config.boot_id = "boot-test";
    config.outbox_directory = outbox;
    config.command_poll_interval = std::chrono::hours(1);
    config.retry_base_delay = std::chrono::hours(1);
    config.retry_max_delay = std::chrono::hours(1);
    auto client = std::make_shared<smarthydro::HttpBackendClient>(
        bus,
        std::vector<std::string>{"zone-1"},
        config,
        transport);
    bus.subscribe(client);
    client->start();

    bus.publish(telemetry_event());

    ASSERT_TRUE(wait_until([&] { return transport->post_count() >= 2; }));
    client->stop();
    std::size_t persisted = 0;
    for (const auto& entry : std::filesystem::directory_iterator(outbox)) {
        if (entry.path().extension() == ".json") {
            ++persisted;
        }
    }
    EXPECT_EQ(persisted, 2U);
    std::filesystem::remove_all(outbox);
}

TEST(HttpBackendClientTest, PollsAndDeserializesRuntimeCommands) {
    smarthydro::EventBus bus;
    const auto outbox = temporary_outbox("commands");
    auto transport = std::make_shared<RecordingTransport>();
    transport->command_response = R"json([
        {
            "command_id": "stop-1",
            "command_type": "EmergencyStop",
            "payload": {"reason": "remote test"}
        }
    ])json";
    smarthydro::HttpBackendConfig config;
    config.boot_id = "boot-test";
    config.outbox_directory = outbox;
    config.command_poll_interval = std::chrono::milliseconds(10);
    auto client = std::make_shared<smarthydro::HttpBackendClient>(
        bus,
        std::vector<std::string>{"zone-1"},
        config,
        transport);
    client->start();

    std::vector<smarthydro::RemoteRuntimeCommand> commands;
    ASSERT_TRUE(wait_until([&] {
        commands = client->take_commands();
        return !commands.empty();
    }));
    client->stop();

    ASSERT_EQ(commands.size(), 1U);
    EXPECT_EQ(commands[0].zone_id, "zone-1");
    EXPECT_EQ(commands[0].envelope.command_id, "stop-1");
    EXPECT_TRUE(std::holds_alternative<smarthydro::EmergencyStopCommand>(
        commands[0].envelope.command));
    EXPECT_EQ(
        std::get<smarthydro::EmergencyStopCommand>(
            commands[0].envelope.command)
            .reason,
        "remote test");
    std::filesystem::remove_all(outbox);
}

TEST(HttpBackendClientTest, DiscoversZonesWithNoLocalConfiguration) {
    smarthydro::EventBus bus;
    const auto outbox = temporary_outbox("zone-discovery");
    auto transport = std::make_shared<RecordingTransport>();
    transport->zone_assignment_response = {
        200,
        R"json([
            {
                "id": "backend-zone-1",
                "assigned_edge_id": "edge-test"
            }
        ])json",
    };
    smarthydro::HttpBackendConfig config;
    config.edge_id = "edge-test";
    config.boot_id = "boot-test";
    config.outbox_directory = outbox;
    config.command_poll_interval = std::chrono::milliseconds(10);
    auto client = std::make_shared<smarthydro::HttpBackendClient>(
        bus,
        std::vector<std::string>{},
        config,
        transport);
    client->start();

    std::vector<std::string> discovered;
    ASSERT_TRUE(wait_until([&] {
        discovered = client->take_discovered_zone_ids();
        return !discovered.empty();
    }));
    client->stop();

    ASSERT_EQ(discovered.size(), 1U);
    EXPECT_EQ(discovered.front(), "backend-zone-1");
    EXPECT_TRUE(std::filesystem::exists(
        outbox / "assigned-zones.json"));
    EXPECT_NE(
        std::find(
            transport->get_paths.begin(),
            transport->get_paths.end(),
            "/api/v1/edges/edge-test/zones"),
        transport->get_paths.end());
    std::filesystem::remove_all(outbox);
}

TEST(HttpBackendClientTest, RestoresDiscoveredZonesWhileBackendIsOffline) {
    smarthydro::EventBus bus;
    const auto outbox = temporary_outbox("cached-zone-discovery");
    smarthydro::HttpBackendConfig config;
    config.edge_id = "edge-test";
    config.boot_id = "boot-test";
    config.outbox_directory = outbox;
    config.command_poll_interval = std::chrono::milliseconds(10);

    {
        auto online_transport = std::make_shared<RecordingTransport>();
        online_transport->zone_assignment_response = {
            200,
            R"json([{"id": "cached-zone"}])json",
        };
        smarthydro::HttpBackendClient online_client(
            bus, {}, config, online_transport);
        online_client.start();
        ASSERT_TRUE(wait_until([&] {
            return !online_client
                        .take_discovered_zone_ids()
                        .empty();
        }));
        online_client.stop();
    }

    auto offline_transport = std::make_shared<RecordingTransport>();
    offline_transport->zone_assignment_response = {503, "offline"};
    smarthydro::HttpBackendClient offline_client(
        bus, {}, config, offline_transport);

    const auto restored =
        offline_client.take_discovered_zone_ids();

    ASSERT_EQ(restored.size(), 1U);
    EXPECT_EQ(restored.front(), "cached-zone");
    std::filesystem::remove_all(outbox);
}

TEST(HttpBackendClientTest, ReconcilesAddedAndRemovedZonesAtomically) {
    smarthydro::EventBus bus;
    const auto outbox = temporary_outbox("zone-reconciliation");
    auto transport = std::make_shared<RecordingTransport>();
    transport->zone_assignment_response = {
        200,
        R"json([
            {"id": "kept-zone"},
            {"id": "added-zone"}
        ])json",
    };
    smarthydro::HttpBackendConfig config;
    config.edge_id = "edge-test";
    config.boot_id = "boot-test";
    config.outbox_directory = outbox;
    config.command_poll_interval = std::chrono::milliseconds(10);
    smarthydro::HttpBackendClient client(
        bus,
        {"kept-zone", "removed-zone"},
        config,
        transport);
    client.start();

    std::vector<std::string> added;
    std::vector<std::string> removed;
    ASSERT_TRUE(wait_until([&] {
        auto new_added = client.take_discovered_zone_ids();
        auto new_removed = client.take_removed_zone_ids();
        added.insert(added.end(), new_added.begin(), new_added.end());
        removed.insert(
            removed.end(), new_removed.begin(), new_removed.end());
        return !added.empty() && !removed.empty();
    }));
    client.stop();

    EXPECT_EQ(added, (std::vector<std::string>{"added-zone"}));
    EXPECT_EQ(removed, (std::vector<std::string>{"removed-zone"}));
    {
        // Scoped so the ifstream (and its underlying file handle) is
        // closed before remove_all below runs — on Windows, deleting a
        // directory while one of its files is still open elsewhere fails
        // with "the process cannot access the file because it is being
        // used by another process"; POSIX allows it, which is why this
        // only ever surfaced there. Same fix as the std::ofstream setup
        // block a few tests down (HttpErrorNeverRemovesKnownAssignments).
        std::ifstream cache(outbox / "assigned-zones.json");
        const auto cached = nlohmann::json::parse(cache);
        EXPECT_EQ(
            cached,
            nlohmann::json::array({"added-zone", "kept-zone"}));
    }
    std::filesystem::remove_all(outbox);
}

TEST(HttpBackendClientTest, ValidEmptyManifestRemovesCachedAssignments) {
    smarthydro::EventBus bus;
    const auto outbox = temporary_outbox("empty-zone-reconciliation");
    auto transport = std::make_shared<RecordingTransport>();
    transport->zone_assignment_response = {200, "[]"};
    transport->command_response = R"json([
        {
            "command_id": "stale-command",
            "command_type": "EmergencyStop",
            "payload": {"reason": "must not execute"}
        }
    ])json";
    smarthydro::HttpBackendConfig config;
    config.edge_id = "edge-test";
    config.boot_id = "boot-test";
    config.outbox_directory = outbox;
    config.command_poll_interval = std::chrono::milliseconds(10);
    smarthydro::HttpBackendClient client(
        bus, {"removed-zone"}, config, transport);
    client.start();

    std::vector<std::string> removed;
    ASSERT_TRUE(wait_until([&] {
        removed = client.take_removed_zone_ids();
        return !removed.empty();
    }));
    client.stop();

    EXPECT_EQ(removed, (std::vector<std::string>{"removed-zone"}));
    EXPECT_TRUE(client.take_commands().empty());
    {
        // See the comment in ReconcilesAddedAndRemovedZonesAtomically
        // above — scoped so the file handle is closed before remove_all.
        std::ifstream cache(outbox / "assigned-zones.json");
        EXPECT_EQ(nlohmann::json::parse(cache), nlohmann::json::array());
    }
    std::filesystem::remove_all(outbox);
}

TEST(HttpBackendClientTest, HttpErrorNeverRemovesKnownAssignments) {
    smarthydro::EventBus bus;
    const auto outbox = temporary_outbox("failed-zone-reconciliation");
    auto transport = std::make_shared<RecordingTransport>();
    transport->zone_assignment_response = {503, "backend offline"};
    std::filesystem::create_directories(outbox);
    {
        std::ofstream cache(outbox / "assigned-zones.json");
        cache << R"json(["known-zone"])json";
    }
    smarthydro::HttpBackendConfig config;
    config.edge_id = "edge-test";
    config.boot_id = "boot-test";
    config.outbox_directory = outbox;
    config.command_poll_interval = std::chrono::milliseconds(10);
    smarthydro::HttpBackendClient client(
        bus, {"known-zone"}, config, transport);
    client.start();

    ASSERT_TRUE(wait_until([&] {
        return std::find(
                   transport->get_paths.begin(),
                   transport->get_paths.end(),
                   "/api/v1/edges/edge-test/zones") !=
               transport->get_paths.end();
    }));
    client.stop();

    EXPECT_TRUE(client.take_removed_zone_ids().empty());
    {
        // See the comment in ReconcilesAddedAndRemovedZonesAtomically
        // above — scoped so the file handle is closed before remove_all.
        std::ifstream cache(outbox / "assigned-zones.json");
        EXPECT_EQ(
            nlohmann::json::parse(cache),
            nlohmann::json::array({"known-zone"}));
    }
    std::filesystem::remove_all(outbox);
}

TEST(HttpBackendClientTest, InvalidManifestNeverRemovesKnownAssignments) {
    smarthydro::EventBus bus;
    const auto outbox = temporary_outbox("invalid-zone-reconciliation");
    auto transport = std::make_shared<RecordingTransport>();
    transport->zone_assignment_response = {200, R"json({"id":"zone-1"})json"};
    smarthydro::HttpBackendConfig config;
    config.edge_id = "edge-test";
    config.boot_id = "boot-test";
    config.outbox_directory = outbox;
    config.command_poll_interval = std::chrono::milliseconds(10);
    smarthydro::HttpBackendClient client(
        bus, {"known-zone"}, config, transport);
    client.start();

    ASSERT_TRUE(wait_until([&] {
        return std::find(
                   transport->get_paths.begin(),
                   transport->get_paths.end(),
                   "/api/v1/edges/edge-test/zones") !=
               transport->get_paths.end();
    }));
    client.stop();

    EXPECT_TRUE(client.take_removed_zone_ids().empty());
    EXPECT_FALSE(std::filesystem::exists(
        outbox / "assigned-zones.json"));
    std::filesystem::remove_all(outbox);
}

TEST(HttpBackendClientTest, DownloadsRecipeReferencedByRemoteCommand) {
    smarthydro::EventBus bus;
    const auto outbox = temporary_outbox("recipe-command");
    auto transport = std::make_shared<RecordingTransport>();
    transport->command_response = R"json([
        {
            "command_id": "recipe-1",
            "command_type": "LoadRecipe",
            "payload": {"recipe_id": "tomato_demo_v1"}
        }
    ])json";
    {
        std::ifstream input(SMARTHYDRO_EXAMPLE_RECIPE_PATH);
        ASSERT_TRUE(input);
        transport->recipe_response =
            nlohmann::json::parse(input).dump();
    }
    smarthydro::HttpBackendConfig config;
    config.boot_id = "boot-test";
    config.outbox_directory = outbox;
    config.command_poll_interval = std::chrono::milliseconds(10);
    auto client = std::make_shared<smarthydro::HttpBackendClient>(
        bus,
        std::vector<std::string>{"zone-1"},
        config,
        transport);
    client->start();

    std::vector<smarthydro::RemoteRuntimeCommand> commands;
    ASSERT_TRUE(wait_until([&] {
        commands = client->take_commands();
        return !commands.empty();
    }));
    client->stop();

    ASSERT_EQ(commands.size(), 1U);
    EXPECT_TRUE(std::holds_alternative<smarthydro::LoadRecipeCommand>(
        commands[0].envelope.command));
    EXPECT_NE(
        std::find(
            transport->get_paths.begin(),
            transport->get_paths.end(),
            "/api/v1/recipes/tomato_demo_v1"),
        transport->get_paths.end());
    std::filesystem::remove_all(outbox);
}

TEST(HttpBackendClientTest, DownloadsRecipeForCultivationActivation) {
    smarthydro::EventBus bus;
    const auto outbox = temporary_outbox("activate-cultivation");
    auto transport = std::make_shared<RecordingTransport>();
    transport->command_response = R"json([
        {
            "command_id": "activate-1",
            "command_type": "ActivateCultivation",
            "payload": {
                "cultivation_id": "cultivation-1",
                "recipe_id": "tomato_demo_v1"
            }
        }
    ])json";
    {
        std::ifstream input(SMARTHYDRO_EXAMPLE_RECIPE_PATH);
        ASSERT_TRUE(input);
        transport->recipe_response =
            nlohmann::json::parse(input).dump();
    }
    smarthydro::HttpBackendConfig config;
    config.boot_id = "boot-test";
    config.outbox_directory = outbox;
    config.command_poll_interval = std::chrono::milliseconds(10);
    auto client = std::make_shared<smarthydro::HttpBackendClient>(
        bus,
        std::vector<std::string>{"zone-1"},
        config,
        transport);
    client->start();

    std::vector<smarthydro::RemoteRuntimeCommand> commands;
    ASSERT_TRUE(wait_until([&] {
        commands = client->take_commands();
        return !commands.empty();
    }));
    client->stop();

    ASSERT_EQ(commands.size(), 1U);
    ASSERT_TRUE(
        std::holds_alternative<
            smarthydro::ActivateCultivationCommand>(
            commands[0].envelope.command));
    const auto& activation =
        std::get<smarthydro::ActivateCultivationCommand>(
            commands[0].envelope.command);
    EXPECT_EQ(activation.cultivation_id, "cultivation-1");
    EXPECT_EQ(activation.recipe.id, "tomato_demo_v1");
    EXPECT_NE(
        std::find(
            transport->get_paths.begin(),
            transport->get_paths.end(),
            "/api/v1/recipes/tomato_demo_v1"),
        transport->get_paths.end());
    std::filesystem::remove_all(outbox);
}

TEST(HttpBackendClientTest, DeserializesZoneLifecycleCommands) {
    const auto pause = smarthydro::runtime_command_from_json(
        R"json({
            "command_id": "pause-1",
            "command_type": "PauseCultivation",
            "payload": {}
        })json");
    const auto resume = smarthydro::runtime_command_from_json(
        R"json({
            "command_id": "resume-1",
            "command_type": "ResumeCultivation",
            "payload": {}
        })json");
    const auto stop = smarthydro::runtime_command_from_json(
        R"json({
            "command_id": "stop-1",
            "command_type": "StopCultivation",
            "payload": {}
        })json");
    const auto speed = smarthydro::runtime_command_from_json(
        R"json({
            "command_id": "speed-1",
            "command_type": "SetSimulationSpeed",
            "payload": {"time_scale": 10.0}
        })json");
    const auto duration = smarthydro::runtime_command_from_json(
        R"json({
            "command_id": "duration-1",
            "command_type": "SetSimulationDuration",
            "payload": {"duration_seconds": 86400.0}
        })json");
    const auto continuous = smarthydro::runtime_command_from_json(
        R"json({
            "command_id": "duration-clear",
            "command_type": "SetSimulationDuration",
            "payload": {"duration_seconds": null}
        })json");

    EXPECT_TRUE(
        std::holds_alternative<
            smarthydro::PauseCultivationCommand>(
            pause.command));
    EXPECT_TRUE(
        std::holds_alternative<
            smarthydro::ResumeCultivationCommand>(
            resume.command));
    EXPECT_TRUE(
        std::holds_alternative<
            smarthydro::StopCultivationCommand>(
            stop.command));
    ASSERT_TRUE(
        std::holds_alternative<
            smarthydro::SetSimulationSpeedCommand>(
            speed.command));
    EXPECT_DOUBLE_EQ(
        std::get<smarthydro::SetSimulationSpeedCommand>(
            speed.command)
            .time_scale,
        10.0);
    ASSERT_TRUE(
        std::holds_alternative<
            smarthydro::SetSimulationDurationCommand>(
            duration.command));
    EXPECT_DOUBLE_EQ(
        *std::get<smarthydro::SetSimulationDurationCommand>(
             duration.command)
             .duration_seconds,
        86400.0);
    ASSERT_TRUE(
        std::holds_alternative<
            smarthydro::SetSimulationDurationCommand>(
            continuous.command));
    EXPECT_FALSE(
        std::get<smarthydro::SetSimulationDurationCommand>(
            continuous.command)
            .duration_seconds
            .has_value());
}

TEST(HttpBackendClientTest, DeserializesTypedFaultCommand) {
    const auto envelope = smarthydro::runtime_command_from_json(
        R"json({
            "command_id": "fault-1",
            "command_type": "InjectFault",
            "payload": {
                "fault_id": "temporary-ph-offset",
                "target_type": "sensor",
                "target": "ph",
                "mode": "sensor_offset",
                "value": 0.4,
                "duration_seconds": 1800.0
            }
        })json");

    ASSERT_TRUE(
        std::holds_alternative<smarthydro::InjectFaultCommand>(
            envelope.command));
    const auto& specification =
        std::get<smarthydro::InjectFaultCommand>(envelope.command)
            .specification;
    EXPECT_EQ(specification.fault_id, "temporary-ph-offset");
    EXPECT_EQ(
        specification.target_kind,
        smarthydro::FaultTargetKind::SENSOR);
    EXPECT_EQ(specification.target, "ph");
    EXPECT_EQ(specification.mode, smarthydro::FaultMode::SENSOR_OFFSET);
    ASSERT_TRUE(specification.value.has_value());
    EXPECT_DOUBLE_EQ(*specification.value, 0.4);
    ASSERT_TRUE(specification.duration_seconds.has_value());
    EXPECT_DOUBLE_EQ(*specification.duration_seconds, 1800.0);
}

TEST(HttpBackendClientTest, SerializesZoneLifecycleEvents) {
    smarthydro::EventBus bus;
    const auto outbox = temporary_outbox("lifecycle-event");
    auto transport = std::make_shared<RecordingTransport>();
    smarthydro::HttpBackendConfig config;
    config.boot_id = "boot-test";
    config.edge_id = "edge-test";
    config.outbox_directory = outbox;
    config.command_poll_interval = std::chrono::hours(1);
    auto client = std::make_shared<smarthydro::HttpBackendClient>(
        bus,
        std::vector<std::string>{"zone-1"},
        config,
        transport);
    bus.subscribe(client);
    client->start();

    bus.publish(
        smarthydro::ZoneLifecycleChanged{
            "zone-1",
            60.0,
            "Running",
            "Paused",
            "operator pause",
        });

    ASSERT_TRUE(wait_until([&] {
        return transport->post_count() >= 1;
    }));
    client->stop();
    const auto posts = transport->post_snapshot();
    ASSERT_EQ(posts.size(), 1U);
    EXPECT_EQ(
        posts[0].first,
        "/api/v1/zones/zone-1/events");
    const auto event = nlohmann::json::parse(posts[0].second);
    EXPECT_EQ(event.at("event_type"), "ZoneLifecycleChanged");
    EXPECT_EQ(
        event.at("payload").at("previous_state"),
        "Running");
    EXPECT_EQ(
        event.at("payload").at("current_state"),
        "Paused");
    std::filesystem::remove_all(outbox);
}

TEST(HttpBackendClientTest, SerializesTemporalEvents) {
    smarthydro::EventBus bus;
    const auto outbox = temporary_outbox("temporal-events");
    auto transport = std::make_shared<RecordingTransport>();
    smarthydro::HttpBackendConfig config;
    config.boot_id = "boot-test";
    config.edge_id = "edge-test";
    config.outbox_directory = outbox;
    config.command_poll_interval = std::chrono::hours(1);
    auto client = std::make_shared<smarthydro::HttpBackendClient>(
        bus,
        std::vector<std::string>{"zone-1"},
        config,
        transport);
    bus.subscribe(client);
    client->start();

    bus.publish(
        smarthydro::SimulationSpeedChanged{
            "zone-1",
            60.0,
            1.0,
            10.0,
        });
    bus.publish(
        smarthydro::SimulationDurationChanged{
            "zone-1",
            60.0,
            true,
            3600.0,
            3660.0,
        });
    bus.publish(
        smarthydro::SimulationDurationCompleted{
            "zone-1",
            3660.0,
            3600.0,
        });
    bus.publish(
        smarthydro::RecipeCompleted{
            "zone-1",
            4000.0,
            "recipe-pomodorino",
            "Produzione e maturazione",
            3024.0,
        });
    bus.publish(
        smarthydro::SchedulerLagStateChanged{
            "zone-1",
            900.0,
            true,
            1800.0,
            2,
            10.0,
        });
    bus.publish(
        smarthydro::FaultDetected{
            "zone-1",
            910.0,
            "water_pump",
            "active_without_command",
            smarthydro::ControlFaultSeverity::CRITICAL,
            "pump is physically active without a command",
        });

    ASSERT_TRUE(wait_until([&] {
        return transport->post_count() >= 6;
    }));
    client->stop();
    const auto posts = transport->post_snapshot();
    ASSERT_EQ(posts.size(), 6U);

    const auto speed = nlohmann::json::parse(posts[0].second);
    EXPECT_EQ(speed.at("event_type"), "SimulationSpeedChanged");
    EXPECT_EQ(
        speed.at("payload").at("previous_time_scale"),
        1.0);
    EXPECT_EQ(
        speed.at("payload").at("current_time_scale"),
        10.0);

    const auto duration = nlohmann::json::parse(posts[1].second);
    EXPECT_EQ(
        duration.at("event_type"),
        "SimulationDurationChanged");
    EXPECT_TRUE(duration.at("payload").at("limited"));
    EXPECT_EQ(
        duration.at("payload").at("duration_seconds"),
        3600.0);
    EXPECT_EQ(
        duration.at("payload").at("target_timestamp_seconds"),
        3660.0);

    const auto completed = nlohmann::json::parse(posts[2].second);
    EXPECT_EQ(
        completed.at("event_type"),
        "SimulationDurationCompleted");
    EXPECT_EQ(
        completed.at("payload").at("duration_seconds"),
        3600.0);

    const auto recipe_completed = nlohmann::json::parse(posts[3].second);
    EXPECT_EQ(recipe_completed.at("event_type"), "RecipeCompleted");
    EXPECT_EQ(
        recipe_completed.at("payload").at("recipe_id"),
        "recipe-pomodorino");
    EXPECT_EQ(
        recipe_completed.at("payload").at("final_phase"),
        "Produzione e maturazione");

    const auto lag = nlohmann::json::parse(posts[4].second);
    EXPECT_EQ(lag.at("event_type"), "SchedulerLagStateChanged");
    EXPECT_TRUE(lag.at("payload").at("lagging"));
    EXPECT_EQ(lag.at("payload").at("pending_steps"), 2);
    EXPECT_EQ(
        lag.at("payload").at("pending_simulation_seconds"),
        1800.0);
    EXPECT_EQ(lag.at("payload").at("time_scale"), 10.0);

    const auto fault = nlohmann::json::parse(posts[5].second);
    EXPECT_EQ(fault.at("event_type"), "FaultDetected");
    EXPECT_EQ(fault.at("payload").at("component"), "water_pump");
    EXPECT_EQ(
        fault.at("payload").at("rule"),
        "active_without_command");
    EXPECT_EQ(fault.at("payload").at("severity"), "Critical");
    std::filesystem::remove_all(outbox);
}

}  // namespace
