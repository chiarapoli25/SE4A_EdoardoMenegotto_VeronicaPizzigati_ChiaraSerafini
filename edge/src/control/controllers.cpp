#include <smarthydro/control/controllers.hpp>

#include <algorithm>
#include <cmath>
#include <stdexcept>
#include <string>

namespace smarthydro {
namespace {

void require_finite(double value, const char* field_name) {
    if (!std::isfinite(value)) {
        throw std::invalid_argument(std::string(field_name) + " must be finite");
    }
}

void validate_command_limits(const CommandLimits& limits) {
    require_finite(limits.minimum, "minimum command");
    require_finite(limits.maximum, "maximum command");
    if (limits.minimum > limits.maximum) {
        throw std::invalid_argument("minimum command must not exceed maximum command");
    }
}

double directed_error(
    double setpoint,
    double measured_value,
    ControlDirection direction) noexcept {
    if (direction == ControlDirection::INCREASES_PROCESS_VALUE) {
        return setpoint - measured_value;
    }
    return measured_value - setpoint;
}

std::optional<double> process_value(const ControllerInput& input) {
    if (input.measured_value.has_value()) {
        return input.measured_value;
    }
    return input.model_estimate;
}

}  // namespace

const char* to_string(StrategyType type) noexcept {
    switch (type) {
        case StrategyType::THRESHOLD:
            return "Threshold";
        case StrategyType::PID:
            return "PID";
        case StrategyType::PREDICTIVE:
            return "Predictive";
    }
    return "Unknown";
}

ThresholdController::ThresholdController(
    double lower_threshold,
    double upper_threshold,
    ControlDirection direction,
    double active_command,
    double inactive_command)
    : ThresholdController(ThresholdConfig{
          lower_threshold,
          upper_threshold,
          direction,
          active_command,
          inactive_command,
          false}) {}

ThresholdController::ThresholdController(ThresholdConfig config)
    : lower_threshold_(config.lower_threshold),
      upper_threshold_(config.upper_threshold),
      direction_(config.direction),
      active_command_(config.active_command),
      inactive_command_(config.inactive_command),
      bidirectional_(config.bidirectional) {
    require_finite(lower_threshold_, "lower threshold");
    require_finite(upper_threshold_, "upper threshold");
    if (lower_threshold_ >= upper_threshold_) {
        throw std::invalid_argument("lower threshold must be less than upper threshold");
    }
    require_finite(active_command_, "active command");
    require_finite(inactive_command_, "inactive command");
}

double ThresholdController::update(double measured_value) {
    require_finite(measured_value, "measured value");

    if (direction_ == ControlDirection::INCREASES_PROCESS_VALUE) {
        if (measured_value < lower_threshold_) {
            active_ = true;
        } else if (measured_value > upper_threshold_) {
            active_ = false;
        }
    } else {
        if (measured_value > upper_threshold_) {
            active_ = true;
        } else if (measured_value < lower_threshold_) {
            active_ = false;
        }
    }

    return active_ ? active_command_ : inactive_command_;
}

StrategyType ThresholdController::strategy_type() const noexcept {
    return StrategyType::THRESHOLD;
}

ControllerResult ThresholdController::compute(const ControllerInput& input) {
    const auto value = process_value(input);
    if (!value.has_value()) {
        return {false, 0.0, std::nullopt, "missing process value"};
    }
    if (!std::isfinite(*value)) {
        return {false, 0.0, std::nullopt, "process value must be finite"};
    }
    if (bidirectional_) {
        if (*value < lower_threshold_) {
            return {true, std::abs(active_command_), std::nullopt, {}};
        }
        if (*value > upper_threshold_) {
            return {true, -std::abs(active_command_), std::nullopt, {}};
        }
        return {true, 0.0, std::nullopt, {}};
    }
    return {true, update(*value), std::nullopt, {}};
}

void ThresholdController::reset() noexcept {
    active_ = false;
}

PidController::PidController(PidConfig config)
    : config_(config) {
    require_finite(config_.setpoint, "PID setpoint");
    require_finite(config_.proportional_gain, "proportional gain");
    require_finite(config_.integral_gain, "integral gain");
    require_finite(config_.derivative_gain, "derivative gain");
    if (config_.proportional_gain < 0.0 || config_.integral_gain < 0.0 ||
        config_.derivative_gain < 0.0) {
        throw std::invalid_argument("PID gains must not be negative");
    }
    validate_command_limits(config_.command_limits);
}

double PidController::update(double measured_value, double delta_time_seconds) {
    require_finite(measured_value, "measured value");
    require_finite(delta_time_seconds, "delta time");
    if (delta_time_seconds <= 0.0) {
        throw std::invalid_argument("delta time must be positive");
    }

    const double error = directed_error(config_.setpoint, measured_value, config_.direction);
    const double derivative = previous_error_.has_value()
                                  ? (error - *previous_error_) / delta_time_seconds
                                  : 0.0;
    const double candidate_integral = integral_ + error * delta_time_seconds;

    const double proportional_term = config_.proportional_gain * error;
    const double derivative_term = config_.derivative_gain * derivative;
    double unconstrained_command = proportional_term +
                                   config_.integral_gain * candidate_integral +
                                   derivative_term;

    const bool winds_up_high =
        unconstrained_command > config_.command_limits.maximum && error > 0.0;
    const bool winds_up_low =
        unconstrained_command < config_.command_limits.minimum && error < 0.0;
    if (!winds_up_high && !winds_up_low) {
        integral_ = candidate_integral;
    } else {
        unconstrained_command = proportional_term + config_.integral_gain * integral_ +
                                derivative_term;
    }

    const double command = std::clamp(
        unconstrained_command,
        config_.command_limits.minimum,
        config_.command_limits.maximum);
    previous_error_ = error;
    return command;
}

StrategyType PidController::strategy_type() const noexcept {
    return StrategyType::PID;
}

ControllerResult PidController::compute(const ControllerInput& input) {
    const auto value = process_value(input);
    if (!value.has_value()) {
        return {false, 0.0, std::nullopt, "missing process value"};
    }
    try {
        return {
            true,
            update(*value, input.delta_time_seconds),
            std::nullopt,
            {}};
    } catch (const std::invalid_argument& error) {
        return {false, 0.0, std::nullopt, error.what()};
    }
}

void PidController::reset() noexcept {
    integral_ = 0.0;
    previous_error_.reset();
}

PredictiveController::PredictiveController(PredictiveConfig config)
    : config_(config) {
    require_finite(config_.setpoint, "predictive setpoint");
    require_finite(config_.prediction_horizon_steps, "prediction horizon");
    require_finite(config_.response_gain, "response gain");
    require_finite(config_.neutral_command, "neutral command");
    require_finite(config_.water_dilution_gain, "water dilution gain");
    require_finite(config_.cumulative_dose_gain, "cumulative dose gain");
    require_finite(config_.substrate_gain, "substrate gain");
    validate_command_limits(config_.command_limits);
    if (config_.prediction_horizon_steps < 0.0) {
        throw std::invalid_argument("prediction horizon must not be negative");
    }
    if (config_.response_gain < 0.0 || config_.water_dilution_gain < 0.0 ||
        config_.cumulative_dose_gain < 0.0 ||
        config_.substrate_gain < 0.0) {
        throw std::invalid_argument("predictive gains must not be negative");
    }
    if (config_.neutral_command < config_.command_limits.minimum ||
        config_.neutral_command > config_.command_limits.maximum) {
        throw std::invalid_argument("neutral command must be within command limits");
    }
}

PredictiveControlResult PredictiveController::update(double measured_value) {
    require_finite(measured_value, "measured value");

    const double trend = previous_measurement_.has_value()
                             ? measured_value - *previous_measurement_
                             : 0.0;
    const double predicted_value =
        measured_value + trend * config_.prediction_horizon_steps;
    const double error = directed_error(
        config_.setpoint, predicted_value, config_.direction);
    const double command = std::clamp(
        config_.neutral_command + config_.response_gain * error,
        config_.command_limits.minimum,
        config_.command_limits.maximum);

    previous_measurement_ = measured_value;
    return {trend, predicted_value, command};
}

StrategyType PredictiveController::strategy_type() const noexcept {
    return StrategyType::PREDICTIVE;
}

ControllerResult PredictiveController::compute(const ControllerInput& input) {
    const auto value = process_value(input);
    if (!value.has_value()) {
        return {false, 0.0, std::nullopt, "missing process or model value"};
    }
    if (!std::isfinite(*value) ||
        !std::isfinite(input.water_delivered_liters) ||
        !std::isfinite(input.cumulative_dose_milliliters) ||
        !std::isfinite(input.phase_target_dose_milliliters) ||
        !std::isfinite(input.substrate_factor)) {
        return {false, 0.0, std::nullopt, "predictive context must be finite"};
    }
    if (input.water_delivered_liters < 0.0 ||
        input.cumulative_dose_milliliters < 0.0 ||
        input.phase_target_dose_milliliters < 0.0 ||
        input.substrate_factor <= 0.0) {
        return {
            false,
            0.0,
            std::nullopt,
            "predictive context must not contain negative quantities"};
    }

    const double trend = previous_measurement_.has_value()
                             ? *value - *previous_measurement_
                             : 0.0;
    const double predicted =
        *value + trend * config_.prediction_horizon_steps -
        config_.water_dilution_gain * input.water_delivered_liters +
        config_.substrate_gain * (input.substrate_factor - 1.0);
    const double remaining_phase_dose = std::max(
        0.0,
        input.phase_target_dose_milliliters -
            input.cumulative_dose_milliliters);
    const double command = std::clamp(
        config_.neutral_command +
            config_.response_gain *
                directed_error(config_.setpoint, predicted, config_.direction) +
            config_.cumulative_dose_gain * remaining_phase_dose,
        config_.command_limits.minimum,
        config_.command_limits.maximum);
    previous_measurement_ = *value;
    return {true, command, predicted, {}};
}

void PredictiveController::reset() noexcept {
    previous_measurement_.reset();
}

std::unique_ptr<IController> ControllerFactory::create(
    StrategyType type,
    const ControllerParameters& parameters) {
    switch (type) {
        case StrategyType::THRESHOLD:
            if (const auto* config = std::get_if<ThresholdConfig>(&parameters)) {
                return std::make_unique<ThresholdController>(*config);
            }
            break;
        case StrategyType::PID:
            if (const auto* config = std::get_if<PidConfig>(&parameters)) {
                return std::make_unique<PidController>(*config);
            }
            break;
        case StrategyType::PREDICTIVE:
            if (const auto* config = std::get_if<PredictiveConfig>(&parameters)) {
                return std::make_unique<PredictiveController>(*config);
            }
            break;
    }
    throw std::invalid_argument(
        "controller parameters do not match selected strategy");
}

}  // namespace smarthydro
