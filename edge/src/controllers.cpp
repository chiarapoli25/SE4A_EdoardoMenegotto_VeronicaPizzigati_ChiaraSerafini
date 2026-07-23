#include "smarthydro/controllers.hpp"

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

}  // namespace

ThresholdController::ThresholdController(
    double lower_threshold,
    double upper_threshold,
    ControlDirection direction,
    double active_command,
    double inactive_command)
    : lower_threshold_(lower_threshold),
      upper_threshold_(upper_threshold),
      direction_(direction),
      active_command_(active_command),
      inactive_command_(inactive_command) {
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

void ThresholdController::reset(bool active) noexcept {
    active_ = active;
}

bool ThresholdController::is_active() const noexcept {
    return active_;
}

PidController::PidController(PidConfig config)
    : config_(config), last_command_(config.command_limits.minimum) {
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

    last_command_ = std::clamp(
        unconstrained_command,
        config_.command_limits.minimum,
        config_.command_limits.maximum);
    previous_error_ = error;
    return last_command_;
}

void PidController::reset() noexcept {
    integral_ = 0.0;
    previous_error_.reset();
    last_command_ = config_.command_limits.minimum;
}

double PidController::last_command() const noexcept {
    return last_command_;
}

PredictiveController::PredictiveController(PredictiveConfig config)
    : config_(config) {
    require_finite(config_.setpoint, "predictive setpoint");
    require_finite(config_.prediction_horizon_steps, "prediction horizon");
    require_finite(config_.response_gain, "response gain");
    require_finite(config_.neutral_command, "neutral command");
    validate_command_limits(config_.command_limits);
    if (config_.prediction_horizon_steps < 0.0) {
        throw std::invalid_argument("prediction horizon must not be negative");
    }
    if (config_.response_gain < 0.0) {
        throw std::invalid_argument("response gain must not be negative");
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

void PredictiveController::reset() noexcept {
    previous_measurement_.reset();
}

}  // namespace smarthydro
