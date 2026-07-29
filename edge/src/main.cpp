#include "smarthydro/edge_runtime.hpp"
#include "smarthydro/recipe_json.hpp"

#include <nlohmann/json.hpp>

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
using Json = nlohmann::json;

enum class OutputFormat {
    HUMAN,
    JSON,
};

struct CommandLineOptions {
    std::filesystem::path recipe_path = SMARTHYDRO_DEFAULT_RECIPE_PATH;
    std::size_t steps = 1;
    double step_seconds = kDefaultStepSeconds;
    OutputFormat output = OutputFormat::HUMAN;
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
        if (argument == "--output") {
            if (++index >= argc) {
                throw std::invalid_argument(
                    "--output requires human or json");
            }
            const std::string format = argv[index];
            if (format == "human") {
                options.output = OutputFormat::HUMAN;
            } else if (format == "json") {
                options.output = OutputFormat::JSON;
            } else {
                throw std::invalid_argument(
                    "--output requires human or json");
            }
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
        << "  --output FORMAT     Output format: human or json (default: human)\n"
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
        << " | t=" << result.start_time_seconds / 3600.0 << " h"
        << " | phase=" << result.phase_name << '\n'
        << "Sensors: soil=";
    print_optional(result.readings.soil_moisture_percent, "%");
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

Json optional_number(const std::optional<double>& value) {
    return value.has_value() ? Json(*value) : Json(nullptr);
}

Json fertilizer_bools(
    const smarthydro::FertilizerValues<bool>& values) {
    Json result = Json::object();
    for (const auto type : {
             smarthydro::FertilizerType::NITROGEN,
             smarthydro::FertilizerType::PHOSPHORUS,
             smarthydro::FertilizerType::POTASSIUM,
             smarthydro::FertilizerType::PH_UP,
             smarthydro::FertilizerType::PH_DOWN}) {
        result[smarthydro::to_string(type)] =
            values[smarthydro::fertilizer_index(type)];
    }
    return result;
}

Json fertilizer_numbers(
    const smarthydro::FertilizerValues<double>& values) {
    Json result = Json::object();
    for (const auto type : {
             smarthydro::FertilizerType::NITROGEN,
             smarthydro::FertilizerType::PHOSPHORUS,
             smarthydro::FertilizerType::POTASSIUM,
             smarthydro::FertilizerType::PH_UP,
             smarthydro::FertilizerType::PH_DOWN}) {
        result[smarthydro::to_string(type)] =
            values[smarthydro::fertilizer_index(type)];
    }
    return result;
}

Json step_to_json(
    std::size_t cycle,
    const smarthydro::EdgeStepResult& result) {
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

    Json decisions = Json::object();
    for (const auto variable : variables) {
        const auto& decision =
            result.decisions[
                smarthydro::controlled_variable_index(variable)];
        decisions[smarthydro::to_string(variable)] = {
            {"status", status_name(decision.status)},
            {"command", decision.command},
            {"actuator", smarthydro::to_string(decision.actuator)},
            {"message", decision.message},
            {"predicted_value", optional_number(decision.predicted_value)},
        };
    }

    const auto& readings = result.readings;
    const auto& environment = result.environment_state;
    const auto& command = result.actuator_command;
    const auto& output = result.actuator_output;

    return {
        {"cycle", cycle},
        {"start_time_seconds", result.start_time_seconds},
        {"duration_seconds", result.duration_seconds},
        {"phase_name", result.phase_name},
        {"sensors", {
            {"timestamp_seconds", readings.timestamp_seconds},
            {"temperature_c", optional_number(readings.temperature_c)},
            {"air_humidity_percent",
             optional_number(readings.air_humidity_percent)},
            {"soil_moisture_percent",
             optional_number(readings.soil_moisture_percent)},
            {"ph", optional_number(readings.ph)},
            {"light_ppfd_umol_m2_s",
             optional_number(readings.light_ppfd_umol_m2_s)},
        }},
        {"models", {
            {"ec_ms_cm", environment.ec_ms_cm},
            {"nitrogen_mg_per_liter",
             environment.nitrogen_mg_per_liter},
            {"phosphorus_mg_per_liter",
             environment.phosphorus_mg_per_liter},
            {"potassium_mg_per_liter",
             environment.potassium_mg_per_liter},
        }},
        {"decisions", std::move(decisions)},
        {"actuators", {
            {"command", {
                {"requested_irrigation_volume_liters",
                 command.requested_irrigation_volume_liters},
                {"fertilizer_valves_open",
                 fertilizer_bools(command.fertilizer_valves_open)},
                {"lighting_percent", command.lighting_percent},
            }},
            {"output", {
                {"water_pump_on", output.water_pump_on},
                {"water_pump_flow_liters_per_hour",
                 output.water_pump_flow_liters_per_hour},
                {"irrigation_volume_liters_last_step",
                 output.irrigation_volume_liters_last_step},
                {"water_pump_on_time_seconds_last_step",
                 output.water_pump_on_time_seconds_last_step},
                {"remaining_irrigation_volume_liters",
                 output.remaining_irrigation_volume_liters},
                {"fertilizer_valves_open",
                 fertilizer_bools(output.fertilizer_valves_open)},
                {"fertilizer_flow_milliliters_per_hour",
                 fertilizer_numbers(
                     output.fertilizer_flow_milliliters_per_hour)},
                {"fertilizer_volume_milliliters_last_step",
                 fertilizer_numbers(
                     output.fertilizer_volume_milliliters_last_step)},
                {"lighting_power_watts", output.lighting_power_watts},
            }},
        }},
        {"delivered", {
            {"water_liters", result.delivered_water_liters},
            {"fertilizer_milliliters",
             fertilizer_numbers(
                 result.delivered_fertilizer_milliliters)},
        }},
        {"environment", {
            {"simulation_time_seconds",
             environment.simulation_time_seconds},
            {"temperature_c", environment.temperature_c},
            {"air_humidity_percent",
             environment.air_humidity_percent},
            {"soil_moisture_percent",
             environment.soil_moisture_percent},
            {"ph", environment.ph},
            {"ec_ms_cm", environment.ec_ms_cm},
            {"light_ppfd_umol_m2_s",
             environment.light_ppfd_umol_m2_s},
            {"nitrogen_mg_per_liter",
             environment.nitrogen_mg_per_liter},
            {"phosphorus_mg_per_liter",
             environment.phosphorus_mg_per_liter},
            {"potassium_mg_per_liter",
             environment.potassium_mg_per_liter},
        }},
    };
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
        Json json_output;
        if (options.output == OutputFormat::JSON) {
            json_output = {
                {"edge", {
                    {"name", "SmartHydro Edge Controller"},
                    {"version", "0.1.0"},
                    {"status", "recipe runtime ready"},
                }},
                {"recipe", {
                    {"id", active_recipe.id},
                    {"plant_type", active_recipe.plant_type},
                    {"version", active_recipe.version},
                    {"file", std::filesystem::absolute(
                        options.recipe_path).string()},
                }},
                {"steps", Json::array()},
            };
        } else {
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
        }

        for (std::size_t step = 1;
             step <= options.steps;
             ++step) {
            const auto result = runtime.step(options.step_seconds);
            if (options.output == OutputFormat::JSON) {
                json_output["steps"].push_back(
                    step_to_json(step, result));
            } else {
                print_step(step, options.steps, result);
            }
        }
        if (options.output == OutputFormat::JSON) {
            std::cout << json_output.dump() << '\n';
        }
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "edge: " << error.what() << '\n';
        return 1;
    }
}
