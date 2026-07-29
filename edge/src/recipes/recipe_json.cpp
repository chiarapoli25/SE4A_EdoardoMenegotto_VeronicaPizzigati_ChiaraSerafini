#include <smarthydro/recipes/recipe_json.hpp>

#include <nlohmann/json.hpp>

#include <fstream>
#include <sstream>
#include <stdexcept>
#include <string>
#include <utility>

namespace smarthydro {
namespace {

using Json = nlohmann::ordered_json;

StrategyType strategy_from_string(const std::string& value) {
    if (value == "Threshold") {
        return StrategyType::THRESHOLD;
    }
    if (value == "PID") {
        return StrategyType::PID;
    }
    if (value == "Predictive") {
        return StrategyType::PREDICTIVE;
    }
    throw std::invalid_argument("unknown StrategyType: " + value);
}

ControlDirection direction_from_string(const std::string& value) {
    if (value == "increases") {
        return ControlDirection::INCREASES_PROCESS_VALUE;
    }
    if (value == "decreases") {
        return ControlDirection::DECREASES_PROCESS_VALUE;
    }
    throw std::invalid_argument("unknown ControlDirection: " + value);
}

const char* direction_to_string(ControlDirection direction) {
    return direction == ControlDirection::INCREASES_PROCESS_VALUE
               ? "increases"
               : "decreases";
}

ControlledVariable variable_from_string(const std::string& value) {
    for (std::size_t index = 0; index < kControlledVariableCount; ++index) {
        const auto variable = static_cast<ControlledVariable>(index);
        if (value == to_string(variable)) {
            return variable;
        }
    }
    throw std::invalid_argument("unknown ControlledVariable: " + value);
}

SensorType sensor_from_string(const std::string& value) {
    for (const auto sensor : {
             SensorType::SOIL_MOISTURE_SENSOR,
             SensorType::LIGHT_SENSOR,
             SensorType::PH_SENSOR,
             SensorType::NITROGEN_MODEL,
             SensorType::PHOSPHORUS_MODEL,
             SensorType::POTASSIUM_MODEL}) {
        if (value == to_string(sensor)) {
            return sensor;
        }
    }
    throw std::invalid_argument("unknown SensorType: " + value);
}

ActuatorType actuator_from_string(const std::string& value) {
    for (const auto actuator : {
             ActuatorType::WATER_PUMP,
             ActuatorType::LIGHTING,
             ActuatorType::PH_CORRECTOR_VALVES,
             ActuatorType::NITROGEN_VALVE,
             ActuatorType::PHOSPHORUS_VALVE,
             ActuatorType::POTASSIUM_VALVE}) {
        if (value == to_string(actuator)) {
            return actuator;
        }
    }
    throw std::invalid_argument("unknown ActuatorType: " + value);
}

ConfirmationState confirmation_from_string(const std::string& value) {
    for (const auto state : {
             ConfirmationState::PENDING_CONFIRMATION,
             ConfirmationState::CONFIRMED,
             ConfirmationState::REJECTED,
             ConfirmationState::INVALID}) {
        if (value == to_string(state)) {
            return state;
        }
    }
    throw std::invalid_argument("unknown ConfirmationState: " + value);
}

SoilType soil_from_string(const std::string& value) {
    if (value == "aerated-universal") {
        return SoilType::AERATED_UNIVERSAL;
    }
    if (value == "draining") {
        return SoilType::DRAINING;
    }
    if (value == "organic-retentive") {
        return SoilType::ORGANIC_RETENTIVE;
    }
    throw std::invalid_argument("unknown SoilType: " + value);
}

Json range_to_json(const ValueRange& range) {
    return {{"minimum", range.minimum}, {"maximum", range.maximum}};
}

ValueRange range_from_json(const Json& json) {
    return {
        json.at("minimum").get<double>(),
        json.at("maximum").get<double>()};
}

Json parameters_to_json(const ControllerParameters& parameters) {
    if (const auto* threshold = std::get_if<ThresholdConfig>(&parameters)) {
        return {
            {"lower_threshold", threshold->lower_threshold},
            {"upper_threshold", threshold->upper_threshold},
            {"direction", direction_to_string(threshold->direction)},
            {"active_command", threshold->active_command},
            {"inactive_command", threshold->inactive_command},
            {"bidirectional", threshold->bidirectional}};
    }
    if (const auto* pid = std::get_if<PidConfig>(&parameters)) {
        return {
            {"setpoint", pid->setpoint},
            {"proportional_gain", pid->proportional_gain},
            {"integral_gain", pid->integral_gain},
            {"derivative_gain", pid->derivative_gain},
            {"command_minimum", pid->command_limits.minimum},
            {"command_maximum", pid->command_limits.maximum},
            {"direction", direction_to_string(pid->direction)}};
    }
    const auto& predictive = std::get<PredictiveConfig>(parameters);
    return {
        {"setpoint", predictive.setpoint},
        {"prediction_horizon_steps", predictive.prediction_horizon_steps},
        {"response_gain", predictive.response_gain},
        {"neutral_command", predictive.neutral_command},
        {"command_minimum", predictive.command_limits.minimum},
        {"command_maximum", predictive.command_limits.maximum},
        {"direction", direction_to_string(predictive.direction)},
        {"water_dilution_gain", predictive.water_dilution_gain},
        {"cumulative_dose_gain", predictive.cumulative_dose_gain},
        {"substrate_gain", predictive.substrate_gain}};
}

ControllerParameters parameters_from_json(
    StrategyType strategy,
    const Json& json) {
    switch (strategy) {
        case StrategyType::THRESHOLD:
            return ThresholdConfig{
                json.at("lower_threshold").get<double>(),
                json.at("upper_threshold").get<double>(),
                direction_from_string(json.at("direction").get<std::string>()),
                json.at("active_command").get<double>(),
                json.at("inactive_command").get<double>(),
                json.value("bidirectional", false)};
        case StrategyType::PID:
            return PidConfig{
                json.at("setpoint").get<double>(),
                json.at("proportional_gain").get<double>(),
                json.at("integral_gain").get<double>(),
                json.at("derivative_gain").get<double>(),
                {json.at("command_minimum").get<double>(),
                 json.at("command_maximum").get<double>()},
                direction_from_string(json.at("direction").get<std::string>())};
        case StrategyType::PREDICTIVE:
            return PredictiveConfig{
                json.at("setpoint").get<double>(),
                json.at("prediction_horizon_steps").get<double>(),
                json.at("response_gain").get<double>(),
                json.at("neutral_command").get<double>(),
                {json.at("command_minimum").get<double>(),
                 json.at("command_maximum").get<double>()},
                direction_from_string(json.at("direction").get<std::string>()),
                json.value("water_dilution_gain", 0.0),
                json.value("cumulative_dose_gain", 0.0),
                json.value("substrate_gain", 0.0)};
    }
    throw std::invalid_argument("unknown strategy parameters");
}

Json safety_to_json(const OutputSafetyLimits& safety) {
    return {
        {"maximum_water_volume_liters", safety.maximum_water_volume_liters},
        {"maximum_pump_duration_seconds", safety.maximum_pump_duration_seconds},
        {"water_pump_flow_liters_per_hour",
         safety.water_pump_flow_liters_per_hour},
        {"maximum_dose_per_command_milliliters",
         safety.maximum_dose_per_command_milliliters},
        {"maximum_daily_dose_milliliters",
         safety.maximum_daily_dose_milliliters},
        {"minimum_seconds_between_doses",
         safety.minimum_seconds_between_doses},
        {"ph_settling_time_seconds", safety.ph_settling_time_seconds}};
}

OutputSafetyLimits safety_from_json(const Json& json) {
    return {
        json.at("maximum_water_volume_liters").get<double>(),
        json.at("maximum_pump_duration_seconds").get<double>(),
        json.at("water_pump_flow_liters_per_hour").get<double>(),
        json.at("maximum_dose_per_command_milliliters").get<double>(),
        json.at("maximum_daily_dose_milliliters").get<double>(),
        json.at("minimum_seconds_between_doses").get<double>(),
        json.at("ph_settling_time_seconds").get<double>()};
}

Json recipe_to_object(const Recipe& recipe) {
    Json phases = Json::array();
    for (const auto& phase : recipe.phases) {
        Json targets = Json::array();
        for (const auto& target : phase.targets) {
            targets.push_back({
                {"variable", to_string(target.variable)},
                {"setpoint", target.setpoint},
                {"allowed_range", range_to_json(target.allowed_range)},
                {"safety_range", range_to_json(target.safety_range)},
                {"suggested_phase_dose_milliliters",
                 target.suggested_phase_dose_milliliters}});
        }
        phases.push_back({
            {"name", phase.name},
            {"duration_hours", phase.duration_hours},
            {"photoperiod",
             {{"start_hour", phase.photoperiod.start_hour},
              {"duration_hours", phase.photoperiod.duration_hours}}},
            {"targets", std::move(targets)}});
    }

    Json controllers = Json::array();
    for (const auto& controller : recipe.controllers) {
        controllers.push_back({
            {"variable", to_string(controller.variable)},
            {"sensor", to_string(controller.sensor)},
            {"actuator", to_string(controller.actuator)},
            {"default_strategy", to_string(controller.default_strategy)},
            {"selected_strategy", to_string(controller.selected_strategy)},
            {"parameters", parameters_to_json(controller.parameters)},
            {"unit", controller.unit},
            {"output_limits", safety_to_json(controller.output_limits)},
            {"confirmation_state", to_string(controller.confirmation_state)},
            {"version", controller.version},
            {"confirmed_recipe_version", controller.confirmed_recipe_version}});
    }

    return {
        {"id", recipe.id},
        {"plant_type", recipe.plant_type},
        {"substrate", to_string(*recipe.substrate)},
        {"version", recipe.version},
        {"phases", std::move(phases)},
        {"controllers", std::move(controllers)}};
}

Recipe recipe_from_object(const Json& json) {
    Recipe recipe;
    recipe.id = json.at("id").get<std::string>();
    recipe.plant_type = json.at("plant_type").get<std::string>();
    recipe.substrate =
        soil_from_string(json.at("substrate").get<std::string>());
    recipe.version = json.at("version").get<std::uint64_t>();

    for (const auto& phase_json : json.at("phases")) {
        RecipePhase phase;
        phase.name = phase_json.at("name").get<std::string>();
        phase.duration_hours =
            phase_json.at("duration_hours").get<double>();
        phase.photoperiod = {
            phase_json.at("photoperiod").at("start_hour").get<double>(),
            phase_json.at("photoperiod").at("duration_hours").get<double>()};
        for (const auto& target_json : phase_json.at("targets")) {
            PhaseVariableTarget target;
            target.variable = variable_from_string(
                target_json.at("variable").get<std::string>());
            target.setpoint = target_json.at("setpoint").get<double>();
            target.allowed_range =
                range_from_json(target_json.at("allowed_range"));
            target.safety_range =
                range_from_json(target_json.at("safety_range"));
            target.suggested_phase_dose_milliliters =
                target_json.value(
                    "suggested_phase_dose_milliliters", 0.0);
            phase.targets[controlled_variable_index(target.variable)] = target;
        }
        recipe.phases.push_back(std::move(phase));
    }

    for (const auto& controller_json : json.at("controllers")) {
        ControllerConfiguration controller;
        controller.variable = variable_from_string(
            controller_json.at("variable").get<std::string>());
        controller.sensor = sensor_from_string(
            controller_json.at("sensor").get<std::string>());
        controller.actuator = actuator_from_string(
            controller_json.at("actuator").get<std::string>());
        controller.default_strategy = strategy_from_string(
            controller_json.at("default_strategy").get<std::string>());
        controller.selected_strategy = strategy_from_string(
            controller_json.at("selected_strategy").get<std::string>());
        controller.parameters = parameters_from_json(
            controller.selected_strategy,
            controller_json.at("parameters"));
        controller.unit = controller_json.at("unit").get<std::string>();
        controller.output_limits =
            safety_from_json(controller_json.at("output_limits"));
        controller.confirmation_state = confirmation_from_string(
            controller_json.value(
                "confirmation_state", "PENDING_CONFIRMATION"));
        controller.version =
            controller_json.value<std::uint64_t>("version", 1);
        controller.confirmed_recipe_version =
            controller_json.value<std::uint64_t>(
                "confirmed_recipe_version", 0);
        recipe.controllers[controlled_variable_index(controller.variable)] =
            std::move(controller);
    }
    RecipeControlSystem::validate_recipe(recipe);
    return recipe;
}

}  // namespace

std::string recipe_to_json(const Recipe& recipe) {
    RecipeControlSystem::validate_recipe(recipe);
    return recipe_to_object(recipe).dump(2);
}

Recipe recipe_from_json(const std::string& json_text) {
    try {
        return recipe_from_object(Json::parse(json_text));
    } catch (const std::exception& error) {
        throw std::invalid_argument(
            std::string("invalid recipe JSON: ") + error.what());
    }
}

Recipe load_recipe_json(const std::string& path) {
    std::ifstream input(path);
    if (!input) {
        throw std::invalid_argument("cannot open recipe file: " + path);
    }
    std::ostringstream buffer;
    buffer << input.rdbuf();
    return recipe_from_json(buffer.str());
}

void save_recipe_json(const Recipe& recipe, const std::string& path) {
    std::ofstream output(path);
    if (!output) {
        throw std::invalid_argument("cannot write recipe file: " + path);
    }
    output << recipe_to_json(recipe) << '\n';
    if (!output) {
        throw std::runtime_error("failed while writing recipe file: " + path);
    }
}

}  // namespace smarthydro
