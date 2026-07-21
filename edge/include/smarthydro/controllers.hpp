#pragma once

#include <optional>

namespace smarthydro {

enum class ControlDirection {
    INCREASES_PROCESS_VALUE,
    DECREASES_PROCESS_VALUE,
};

struct OutputLimits {
    double minimum_percent = 0.0;
    double maximum_percent = 100.0;
};

class ThresholdController {
public:
    ThresholdController(
        double lower_threshold,
        double upper_threshold,
        ControlDirection direction = ControlDirection::INCREASES_PROCESS_VALUE,
        double active_output_percent = 100.0,
        double inactive_output_percent = 0.0);

    double update(double measured_value);
    void reset(bool active = false) noexcept;
    bool is_active() const noexcept;

private:
    double lower_threshold_;
    double upper_threshold_;
    ControlDirection direction_;
    double active_output_percent_;
    double inactive_output_percent_;
    bool active_ = false;
};

struct PidConfig {
    double setpoint = 0.0;
    double proportional_gain = 0.0;
    double integral_gain = 0.0;
    double derivative_gain = 0.0;
    OutputLimits output_limits;
    ControlDirection direction = ControlDirection::INCREASES_PROCESS_VALUE;
};

class PidController {
public:
    explicit PidController(PidConfig config);

    double update(double measured_value, double delta_time_seconds);
    void reset() noexcept;
    double last_output_percent() const noexcept;

private:
    PidConfig config_;
    double integral_ = 0.0;
    std::optional<double> previous_error_;
    double last_output_percent_ = 0.0;
};

struct PredictiveConfig {
    double setpoint = 0.0;
    double prediction_horizon_steps = 1.0;
    double response_gain = 1.0;
    double neutral_output_percent = 0.0;
    OutputLimits output_limits;
    ControlDirection direction = ControlDirection::INCREASES_PROCESS_VALUE;
};

struct PredictiveControlResult {
    double measured_trend;
    double predicted_value;
    double output_percent;
};

class PredictiveController {
public:
    explicit PredictiveController(PredictiveConfig config);

    PredictiveControlResult update(double measured_value);
    void reset() noexcept;

private:
    PredictiveConfig config_;
    std::optional<double> previous_measurement_;
};

}  // namespace smarthydro
