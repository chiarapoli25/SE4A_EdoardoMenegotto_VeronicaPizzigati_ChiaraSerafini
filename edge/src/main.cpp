#include <smarthydro/backend/http_backend_client.hpp>
#include <smarthydro/events/event_bus.hpp>
#include <smarthydro/recipes/recipe_json.hpp>
#include <smarthydro/runtime/greenhouse_manager.hpp>

#include <array>
#include <chrono>
#include <cmath>
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
#include <utility>

namespace {

constexpr double kDefaultStepSeconds = 900.0;

struct CommandLineOptions {
    std::filesystem::path recipe_path = SMARTHYDRO_DEFAULT_RECIPE_PATH;
    std::size_t zones = 1;
    std::size_t steps = 1;
    double step_seconds = kDefaultStepSeconds;
    std::optional<std::string> backend_url;
    std::string edge_id = "smarthydro-edge";
    std::filesystem::path outbox_path = "edge-data/outbox";
    std::size_t command_poll_milliseconds = 1000;
    std::size_t cycle_delay_milliseconds = 0;
    bool show_help = false;
};

const char* status_name(
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

std::size_t parse_positive_size(
    const std::string& value,
    const char* option) {
    std::size_t parsed_characters = 0;
    const auto parsed = std::stoull(value, &parsed_characters);
    if (parsed_characters != value.size() || parsed == 0) {
        throw std::invalid_argument(
            std::string(option) + " requires a positive integer");
    }
    return static_cast<std::size_t>(parsed);
}

double parse_positive_double(
    const std::string& value,
    const char* option) {
    std::size_t parsed_characters = 0;
    const double parsed =
        std::stod(value, &parsed_characters);
    if (parsed_characters != value.size() ||
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
        if (argument == "--recipe") {
            if (++index >= argc) {
                throw std::invalid_argument(
                    "--recipe requires a JSON path");
            }
            options.recipe_path = argv[index];
            continue;
        }
        if (argument == "--steps") {
            if (++index >= argc) {
                throw std::invalid_argument(
                    "--steps requires a value");
            }
            options.steps =
                parse_positive_size(argv[index], "--steps");
            continue;
        }
        if (argument == "--zones") {
            if (++index >= argc) {
                throw std::invalid_argument(
                    "--zones requires a value");
            }
            options.zones =
                parse_positive_size(argv[index], "--zones");
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
            if (++index >= argc) {
                throw std::invalid_argument(
                    "--backend-url requires a URL");
            }
            options.backend_url = argv[index];
            continue;
        }
        if (argument == "--edge-id") {
            if (++index >= argc) {
                throw std::invalid_argument(
                    "--edge-id requires a value");
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
                parse_positive_size(argv[index], "--command-poll-ms");
            continue;
        }
        if (argument == "--cycle-delay-ms") {
            if (++index >= argc) {
                throw std::invalid_argument(
                    "--cycle-delay-ms requires a value");
            }
            std::size_t parsed_characters = 0;
            const std::string value = argv[index];
            const auto parsed = std::stoull(value, &parsed_characters);
            if (
                value.empty() ||
                value.front() == '-' ||
                parsed_characters != value.size()) {
                throw std::invalid_argument(
                    "--cycle-delay-ms requires a non-negative integer");
            }
            options.cycle_delay_milliseconds =
                static_cast<std::size_t>(parsed);
            continue;
        }
        throw std::invalid_argument(
            "unknown argument: " + argument);
    }
    return options;
}

void print_help(const char* executable) {
    std::cout
        << "Usage: " << executable << " [options]\n\n"
        << "Options:\n"
        << "  --recipe PATH       Recipe JSON to load (default: "
        << SMARTHYDRO_DEFAULT_RECIPE_PATH << ")\n"
        << "  --zones N           Number of independent zones (default: 1)\n"
        << "  --steps N           Number of control cycles (default: 1)\n"
        << "  --step-seconds SEC  Simulated seconds per cycle (default: 900)\n"
        << "  --backend-url URL   Enable asynchronous backend delivery\n"
        << "  --edge-id ID        Stable Edge identifier\n"
        << "  --outbox-path PATH  Persistent delivery queue directory\n"
        << "  --command-poll-ms N Command polling interval (default: 1000)\n"
        << "  --cycle-delay-ms N  Real-time pause before each cycle (default: 0)\n"
        << "  -h, --help          Show this help\n";
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
    std::size_t step,
    std::size_t step_count,
    const smarthydro::EdgeStepResult& result) {
    std::cout
        << "\nZone " << zone_id
        << " | cycle " << step << '/' << step_count
        << " | sequence=" << result.sequence_number
        << " | t=" << result.start_time_seconds / 3600.0 << " h"
        << " | phase=" << result.phase_name
        << " | state="
        << smarthydro::to_string(result.operational_state)
        << '\n'
        << "Sensors: soil=";
    print_optional(result.readings.soil_moisture_percent, "%");
    std::cout << ", light=";
    print_optional(
        result.readings.light_ppfd_umol_m2_s,
        " umol/(m2 s)");
    std::cout << ", pH=";
    print_optional(result.readings.ph);
    std::cout << '\n';

    for (const auto& event : result.events) {
        std::cout
            << "Event: " << smarthydro::to_string(event.type)
            << " | t=" << event.timestamp_seconds / 3600.0
            << " h | " << event.message << '\n';
    }

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
    std::cout << "Recipe decisions:\n";
    for (const auto variable : variables) {
        const auto& decision =
            result.decisions[
                smarthydro::controlled_variable_index(variable)];
        std::cout
            << "  " << smarthydro::to_string(variable)
            << ": " << status_name(decision.status)
            << ", command=" << decision.command;
        if (!decision.message.empty()) {
            std::cout << " (" << decision.message << ')';
        }
        std::cout << '\n';
    }

    const auto delivered =
        [&result](smarthydro::FertilizerType type) {
            return result.delivered_fertilizer_milliliters[
                smarthydro::fertilizer_index(type)];
        };
    std::cout
        << "Delivered: water=" << result.delivered_water_liters
        << " L, N="
        << delivered(smarthydro::FertilizerType::NITROGEN)
        << " mL, P="
        << delivered(smarthydro::FertilizerType::PHOSPHORUS)
        << " mL, K="
        << delivered(smarthydro::FertilizerType::POTASSIUM)
        << " mL, pH+="
        << delivered(smarthydro::FertilizerType::PH_UP)
        << " mL, pH-="
        << delivered(smarthydro::FertilizerType::PH_DOWN)
        << " mL\n"
        << "Final state: soil="
        << result.environment_state.soil_moisture_percent
        << "%, light="
        << result.environment_state.light_ppfd_umol_m2_s
        << " umol/(m2 s), pH="
        << result.environment_state.ph
        << ", N="
        << result.environment_state.nitrogen_mg_per_liter
        << ", P="
        << result.environment_state.phosphorus_mg_per_liter
        << ", K="
        << result.environment_state.potassium_mg_per_liter
        << " mg/L\n";
}

}  // namespace

int main(int argc, char* argv[]) {
    try {
        const auto options = parse_options(argc, argv);
        if (options.show_help) {
            print_help(argv[0]);
            return 0;
        }

        const auto recipe =
            smarthydro::load_recipe_json(options.recipe_path.string());
        auto event_bus = std::make_shared<smarthydro::EventBus>();
        auto console_logger =
            std::make_shared<smarthydro::ConsoleLogger>(std::cout);
        event_bus->subscribe(console_logger);
        smarthydro::GreenhouseManager greenhouse(event_bus);
        for (std::size_t index = 0; index < options.zones; ++index) {
            auto& zone = greenhouse.add_simulated_zone(
                "zone-" + std::to_string(index + 1),
                recipe,
                {},
                {},
                {},
                static_cast<std::uint32_t>(0x53484D31U + index),
                static_cast<std::uint32_t>(0x53484D32U + index));
            zone.confirm_all_configurations();
        }

        std::shared_ptr<smarthydro::HttpBackendClient> backend_client;
        if (options.backend_url.has_value()) {
            smarthydro::HttpBackendConfig backend_config;
            backend_config.base_url = *options.backend_url;
            backend_config.edge_id = options.edge_id;
            if (const auto* token =
                    std::getenv("SMARTHYDRO_API_TOKEN")) {
                backend_config.bearer_token = token;
            }
            backend_config.outbox_directory = options.outbox_path;
            backend_config.command_poll_interval =
                std::chrono::milliseconds(
                    options.command_poll_milliseconds);
            backend_client =
                std::make_shared<smarthydro::HttpBackendClient>(
                    *event_bus,
                    greenhouse.zone_ids(),
                    std::move(backend_config));
            event_bus->subscribe(backend_client);
            backend_client->start();
        }

        std::cout
            << "SmartHydro Edge Controller\n"
            << "Version: 0.1.0\n"
            << "Status: recipe runtime ready\n"
            << "Zones: " << greenhouse.size() << "\n"
            << "Recipe: " << recipe.id
            << " v" << recipe.version
            << " (" << recipe.plant_type << ")\n"
            << "Recipe file: "
            << std::filesystem::absolute(options.recipe_path)
            << "\nBackend: "
            << (
                   options.backend_url.has_value()
                       ? *options.backend_url
                       : "offline")
            << "\nConfigurations: locally validated and confirmed\n"
            << std::fixed << std::setprecision(2);

        for (std::size_t step = 1;
             step <= options.steps;
             ++step) {
            if (options.cycle_delay_milliseconds > 0) {
                std::this_thread::sleep_for(
                    std::chrono::milliseconds(
                        options.cycle_delay_milliseconds));
            }
            if (backend_client) {
                for (auto& remote : backend_client->take_commands()) {
                    const auto result = greenhouse.execute_command(
                        remote.zone_id,
                        remote.envelope);
                    backend_client->submit_command_result(
                        remote.zone_id,
                        result);
                }
            }
            const auto results =
                greenhouse.step_all(options.step_seconds);
            for (const auto& [zone_id, result] : results) {
                print_step(
                    zone_id,
                    step,
                    options.steps,
                    result);
            }
        }
        if (backend_client) {
            backend_client->flush(std::chrono::milliseconds(2500));
            backend_client->stop();
        }
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "edge: " << error.what() << '\n';
        return 1;
    }
}
