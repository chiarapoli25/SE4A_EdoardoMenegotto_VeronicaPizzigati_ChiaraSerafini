#include "smarthydro/edge_runtime.hpp"
#include "smarthydro/recipe_json.hpp"

#include <array>
#include <cmath>
#include <cstddef>
#include <filesystem>
#include <iomanip>
#include <iostream>
#include <optional>
#include <stdexcept>
#include <string>
#include <utility>

namespace {

constexpr double kDefaultStepSeconds = 900.0;

struct CommandLineOptions {
    std::filesystem::path recipe_path = SMARTHYDRO_DEFAULT_RECIPE_PATH;
    std::size_t steps = 1;
    double step_seconds = kDefaultStepSeconds;
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
        if (argument == "--step-seconds") {
            if (++index >= argc) {
                throw std::invalid_argument(
                    "--step-seconds requires a value");
            }
            options.step_seconds = parse_positive_double(
                argv[index], "--step-seconds");
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
        << "  --steps N           Number of control cycles (default: 1)\n"
        << "  --step-seconds SEC  Simulated seconds per cycle (default: 900)\n"
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
    std::size_t step,
    std::size_t step_count,
    const smarthydro::EdgeStepResult& result) {
    std::cout
        << "\nCycle " << step << '/' << step_count
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

        auto recipe =
            smarthydro::load_recipe_json(options.recipe_path.string());
        smarthydro::EdgeRuntime runtime(std::move(recipe));
        runtime.confirm_all_configurations();

        const auto& active_recipe =
            runtime.control_system().recipe();
        std::cout
            << "SmartHydro Edge Controller\n"
            << "Version: 0.1.0\n"
            << "Status: recipe runtime ready\n"
            << "Recipe: " << active_recipe.id
            << " v" << active_recipe.version
            << " (" << active_recipe.plant_type << ")\n"
            << "Recipe file: "
            << std::filesystem::absolute(options.recipe_path)
            << "\nConfigurations: locally validated and confirmed\n"
            << std::fixed << std::setprecision(2);

        for (std::size_t step = 1;
             step <= options.steps;
             ++step) {
            print_step(
                step,
                options.steps,
                runtime.step(options.step_seconds));
        }
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "edge: " << error.what() << '\n';
        return 1;
    }
}
