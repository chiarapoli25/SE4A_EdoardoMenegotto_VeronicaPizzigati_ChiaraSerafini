#include <smarthydro/backend/http_backend_client.hpp>
#include <smarthydro/events/event_bus.hpp>
#include <smarthydro/runtime/greenhouse_manager.hpp>
#include <smarthydro/runtime/simulation_scheduler.hpp>

#include <chrono>
#include <cmath>
#include <csignal>
#include <cstddef>
#include <cstdlib>
#include <filesystem>
#include <iomanip>
#include <iostream>
#include <memory>
#include <optional>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

namespace {

constexpr double kDefaultStepSeconds = 900.0;
constexpr std::size_t kDefaultServiceLoopMilliseconds = 100;
constexpr std::size_t kDefaultMaximumCatchUpSteps = 8;

volatile std::sig_atomic_t stop_requested = 0;

void request_stop(int) {
    stop_requested = 1;
}

struct CommandLineOptions {
    std::size_t zones = 0;
    std::vector<std::string> zone_ids;
    double step_seconds = kDefaultStepSeconds;
    std::string backend_url = "http://127.0.0.1:8000";
    std::string edge_id = "smarthydro-edge";
    std::filesystem::path outbox_path = "edge-data/outbox";
    std::size_t command_poll_milliseconds = 1000;
    std::size_t service_loop_milliseconds =
        kDefaultServiceLoopMilliseconds;
    std::size_t max_catch_up_steps =
        kDefaultMaximumCatchUpSteps;
    bool zones_option_used = false;
    bool show_help = false;
};

std::size_t parse_positive_size(
    const std::string& value,
    const char* option) {
    std::size_t parsed_characters = 0;
    const auto parsed = std::stoull(value, &parsed_characters);
    if (
        value.empty() ||
        value.front() == '-' ||
        parsed_characters != value.size() ||
        parsed == 0) {
        throw std::invalid_argument(
            std::string(option) + " requires a positive integer");
    }
    return static_cast<std::size_t>(parsed);
}

double parse_positive_double(
    const std::string& value,
    const char* option) {
    std::size_t parsed_characters = 0;
    const double parsed = std::stod(value, &parsed_characters);
    if (
        parsed_characters != value.size() ||
        !std::isfinite(parsed) ||
        parsed <= 0.0) {
        throw std::invalid_argument(
            std::string(option) + " requires a positive number");
    }
    return parsed;
}

CommandLineOptions parse_options(int argc, char* argv[]) {
    CommandLineOptions options;
    for (int index = 1; index < argc; ++index) {
        const std::string argument = argv[index];
        if (argument == "--help" || argument == "-h") {
            options.show_help = true;
            continue;
        }
        if (argument == "--zones") {
            if (++index >= argc) {
                throw std::invalid_argument(
                    "--zones requires a value");
            }
            options.zones =
                parse_positive_size(argv[index], "--zones");
            options.zones_option_used = true;
            continue;
        }
        if (argument == "--zone-id") {
            if (++index >= argc || std::string(argv[index]).empty()) {
                throw std::invalid_argument(
                    "--zone-id requires a non-empty identifier");
            }
            options.zone_ids.emplace_back(argv[index]);
            continue;
        }
        if (argument == "--step-seconds") {
            if (++index >= argc) {
                throw std::invalid_argument(
                    "--step-seconds requires a value");
            }
            options.step_seconds = parse_positive_double(
                argv[index], "--step-seconds");
            continue;
        }
        if (argument == "--backend-url") {
            if (++index >= argc || std::string(argv[index]).empty()) {
                throw std::invalid_argument(
                    "--backend-url requires a URL");
            }
            options.backend_url = argv[index];
            continue;
        }
        if (argument == "--edge-id") {
            if (++index >= argc || std::string(argv[index]).empty()) {
                throw std::invalid_argument(
                    "--edge-id requires a non-empty value");
            }
            options.edge_id = argv[index];
            continue;
        }
        if (argument == "--outbox-path") {
            if (++index >= argc) {
                throw std::invalid_argument(
                    "--outbox-path requires a directory");
            }
            options.outbox_path = argv[index];
            continue;
        }
        if (argument == "--command-poll-ms") {
            if (++index >= argc) {
                throw std::invalid_argument(
                    "--command-poll-ms requires a value");
            }
            options.command_poll_milliseconds =
                parse_positive_size(
                    argv[index],
                    "--command-poll-ms");
            continue;
        }
        if (argument == "--service-loop-ms") {
            if (++index >= argc) {
                throw std::invalid_argument(
                    "--service-loop-ms requires a value");
            }
            options.service_loop_milliseconds =
                parse_positive_size(
                    argv[index],
                    "--service-loop-ms");
            continue;
        }
        if (argument == "--max-catch-up-steps") {
            if (++index >= argc) {
                throw std::invalid_argument(
                    "--max-catch-up-steps requires a value");
            }
            options.max_catch_up_steps =
                parse_positive_size(
                    argv[index],
                    "--max-catch-up-steps");
            continue;
        }
        throw std::invalid_argument(
            "unknown argument: " + argument);
    }
    if (options.zones_option_used && !options.zone_ids.empty()) {
        throw std::invalid_argument(
            "--zones and --zone-id cannot be used together");
    }
    return options;
}

std::vector<std::string> configured_zone_ids(
    const CommandLineOptions& options) {
    if (!options.zone_ids.empty()) {
        return options.zone_ids;
    }
    std::vector<std::string> identifiers;
    identifiers.reserve(options.zones);
    for (std::size_t index = 0; index < options.zones; ++index) {
        identifiers.push_back(
            "zone-" + std::to_string(index + 1));
    }
    return identifiers;
}

void print_help(const char* executable) {
    std::cout
        << "Usage: " << executable << " [options]\n\n"
        << "Starts the Edge as a continuous service without a local recipe.\n"
        << "Registered zones remain inactive until the backend sends an\n"
        << "ActivateCultivation command.\n\n"
        << "Options:\n"
        << "  --zones N           Generate local zone-1..zone-N\n"
        << "  --zone-id ID        Register an explicit zone; repeatable\n"
        << "  --step-seconds SEC  Simulation control quantum"
        << " (default: 900)\n"
        << "  --backend-url URL   Backend URL"
        << " (default: http://127.0.0.1:8000)\n"
        << "  --edge-id ID        Stable Edge identifier\n"
        << "  --outbox-path PATH  Persistent delivery queue directory\n"
        << "  --command-poll-ms N Command polling interval"
        << " (default: 1000)\n"
        << "  --service-loop-ms N Inactive service-loop delay"
        << " (default: 100)\n"
        << "  --max-catch-up-steps N Maximum global steps per loop"
        << " (default: 8)\n"
        << "  -h, --help          Show this help\n";
}

const char* decision_status_name(
    smarthydro::ControlDecisionStatus status) noexcept {
    switch (status) {
        case smarthydro::ControlDecisionStatus::APPLIED:
            return "APPLIED";
        case smarthydro::ControlDecisionStatus::LIMITED:
            return "LIMITED";
        case smarthydro::ControlDecisionStatus::BLOCKED:
            return "BLOCKED";
    }
    return "UNKNOWN";
}

void print_optional(
    const std::optional<double>& value,
    const char* unit = "") {
    if (value.has_value()) {
        std::cout << *value << unit;
    } else {
        std::cout << "dropout";
    }
}

void print_step(
    const std::string& zone_id,
    const smarthydro::EdgeStepResult& result) {
    std::cout
        << "\nZone " << zone_id
        << " | sequence=" << result.sequence_number
        << " | t=" << result.start_time_seconds / 3600.0 << " h"
        << " | phase=" << result.phase_name
        << " | recipe="
        << (result.recipe_completed ? "completed" : "running")
        << " | state="
        << smarthydro::to_string(result.operational_state)
        << "\nSensors: soil=";
    print_optional(result.readings.soil_moisture_percent, "%");
    std::cout << "  EC terreno apparente: ";
    print_optional(result.readings.soil_bulk_ec_ms_cm, " mS/cm");
    std::cout << "  EC stimata acqua nei pori: ";
    print_optional(result.readings.soil_ec_ms_cm, " mS/cm");
    std::cout << "  fertilizzante totale stimato: ";
    print_optional(
        result.readings.fertilizer_concentration_mg_per_liter,
        " mg/L");
    std::cout << ", light=";
    print_optional(
        result.readings.light_ppfd_umol_m2_s,
        " umol/(m2 s)");
    std::cout << ", pH=";
    print_optional(result.readings.ph);
    std::cout << '\n';

    constexpr smarthydro::ControlledValues<
        smarthydro::ControlledVariable>
        variables{{
            smarthydro::ControlledVariable::SOIL_MOISTURE,
            smarthydro::ControlledVariable::LIGHT,
            smarthydro::ControlledVariable::PH,
            smarthydro::ControlledVariable::NITROGEN,
            smarthydro::ControlledVariable::PHOSPHORUS,
            smarthydro::ControlledVariable::POTASSIUM,
        }};
    for (const auto variable : variables) {
        const auto& decision =
            result.decisions[
                smarthydro::controlled_variable_index(variable)];
        std::cout
            << "  " << smarthydro::to_string(variable)
            << ": " << decision_status_name(decision.status)
            << ", command=" << decision.command << '\n';
    }
}

}  // namespace

int main(int argc, char* argv[]) {
    try {
        const auto options = parse_options(argc, argv);
        if (options.show_help) {
            print_help(argv[0]);
            return 0;
        }

        std::signal(SIGINT, request_stop);
        std::signal(SIGTERM, request_stop);

        auto event_bus = std::make_shared<smarthydro::EventBus>();
        auto console_logger =
            std::make_shared<smarthydro::ConsoleLogger>(std::cout);
        event_bus->subscribe(console_logger);

        smarthydro::GreenhouseManager greenhouse(event_bus);
        for (const auto& zone_id : configured_zone_ids(options)) {
            greenhouse.add_inactive_zone(zone_id);
        }

        smarthydro::HttpBackendConfig backend_config;
        backend_config.base_url = options.backend_url;
        backend_config.edge_id = options.edge_id;
        if (const auto* token =
                std::getenv("SMARTHYDRO_API_TOKEN")) {
            backend_config.bearer_token = token;
        }
        backend_config.outbox_directory = options.outbox_path;
        backend_config.command_poll_interval =
            std::chrono::milliseconds(
                options.command_poll_milliseconds);

        auto backend_client =
            std::make_shared<smarthydro::HttpBackendClient>(
                *event_bus,
                greenhouse.zone_ids(),
                std::move(backend_config));
        event_bus->subscribe(backend_client);
        backend_client->start();

        std::cout
            << "SmartHydro Edge Controller\n"
            << "Version: 0.1.0\n"
            << "Status: waiting for cultivation activation\n"
            << "Backend: " << options.backend_url << '\n'
            << "Zones: " << greenhouse.size()
            << " (inactive)\n"
            << "Simulation control quantum: "
            << options.step_seconds << " seconds\n"
            << "Maximum catch-up steps per loop: "
            << options.max_catch_up_steps << '\n'
            << "Press Ctrl+C to stop.\n"
            << std::fixed << std::setprecision(2);

        smarthydro::SimulationScheduler scheduler(
            greenhouse,
            {
                options.step_seconds,
                options.max_catch_up_steps,
            });
        using Clock = smarthydro::SimulationScheduler::Clock;

        while (!stop_requested) {
            const auto now = Clock::now();
            scheduler.accrue(now);
            for (auto& zone_id :
                 backend_client->take_removed_zone_ids()) {
                if (!greenhouse.remove_zone(zone_id)) {
                    continue;
                }
                std::cout
                    << "Decommissioned unassigned backend zone "
                    << zone_id << " from Edge "
                    << options.edge_id << '\n';
            }
            for (auto& zone_id :
                 backend_client->take_discovered_zone_ids()) {
                if (greenhouse.contains(zone_id)) {
                    continue;
                }
                greenhouse.add_inactive_zone(zone_id);
                std::cout
                    << "Provisioned backend zone " << zone_id
                    << " on Edge " << options.edge_id << '\n';
            }
            for (auto& remote : backend_client->take_commands()) {
                if (!greenhouse.contains(remote.zone_id)) {
                    continue;
                }
                const auto result = greenhouse.execute_command(
                    remote.zone_id,
                    remote.envelope);
                backend_client->submit_command_result(
                    remote.zone_id,
                    result);
                std::cout
                    << "Command " << result.command_id
                    << " (" << result.command_type << "): "
                    << smarthydro::to_string(result.status)
                    << " - " << result.message << '\n';
            }
            scheduler.synchronize(now);
            const auto scheduled_results =
                scheduler.run_due_steps();
            for (const auto& [zone_id, results] :
                 scheduled_results) {
                for (const auto& result : results) {
                    print_step(zone_id, result);
                }
            }

            std::this_thread::sleep_for(
                std::chrono::milliseconds(
                    options.service_loop_milliseconds));
        }

        std::cout << "Stopping SmartHydro Edge Controller...\n";
        backend_client->flush(std::chrono::milliseconds(2500));
        backend_client->stop();
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "edge: " << error.what() << '\n';
        return 1;
    }
}
