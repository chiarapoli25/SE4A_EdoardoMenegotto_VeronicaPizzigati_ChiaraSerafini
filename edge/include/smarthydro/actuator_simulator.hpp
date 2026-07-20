#pragma once

#include <optional>
#include <string>

namespace smarthydro {

struct ActuatorState {
    double water_pump_percent = 0.0;
    std::optional<std::string> selected_fertilizer_id;
    double fertilizer_dosing_percent = 0.0;
    double lighting_percent = 0.0;
};

class ActuatorSimulator {
public:
    const ActuatorState& state() const noexcept;
    const ActuatorState& target_state() const noexcept;

    void set_water_pump_target_percent(double value);
    void select_fertilizer(const std::string& fertilizer_id);
    void clear_fertilizer_selection() noexcept;
    void set_fertilizer_dosing_target_percent(double value);
    void set_lighting_target_percent(double value);

    void step() noexcept;
    void stop_all() noexcept;

private:
    ActuatorState state_;
    ActuatorState target_state_;
};

}  // namespace smarthydro
