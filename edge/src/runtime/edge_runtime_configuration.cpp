#include <smarthydro/events/event_bus.hpp>
#include <smarthydro/runtime/edge_runtime.hpp>

#include <limits>
#include <stdexcept>
#include <utility>

namespace smarthydro {
namespace {

constexpr double kSecondsPerHour = 3600.0;

}  // namespace

const std::string& EdgeRuntime::active_phase_name() const {
    return control_system_.recipe()
        .phases[active_phase_index(elapsed_recipe_hours())]
        .name;
}

void EdgeRuntime::change_strategy(
    ControlledVariable variable,
    StrategyType strategy,
    ControllerParameters parameters) {
    const auto index = controlled_variable_index(variable);
    const auto previous_strategy =
        control_system_.recipe().controllers[index].selected_strategy;
    control_system_.select_strategy(
        variable, strategy, std::move(parameters));
    if (event_bus_) {
        event_bus_->publish(
            StrategyChanged{
                zone_id_,
                environment_->state().simulation_time_seconds,
                variable,
                previous_strategy,
                strategy,
            });
    }
}

void EdgeRuntime::replace_recipe(Recipe recipe) {
    RecipeControlSystem::validate_recipe(recipe);
    if (*recipe.substrate != active_substrate_) {
        throw std::invalid_argument(
            "replacement recipe substrate differs from the physical zone");
    }
    actuators_->stop_all();
    control_system_.replace_recipe(std::move(recipe));
    recipe_start_time_seconds_ =
        environment_->state().simulation_time_seconds;
    recipe_time_offset_seconds_ = 0.0;
    reported_phase_index_.reset();
    history_phase_index_ = kControlledVariableCount;
    cumulative_phase_dose_milliliters_.fill(0.0);
    seconds_since_last_dose_.fill(
        std::numeric_limits<double>::max() / 4.0);
}

ConfirmationResult EdgeRuntime::confirm_configuration(
    ControlledVariable variable) {
    return control_system_.confirm_configuration(variable);
}

void EdgeRuntime::reject_configuration(ControlledVariable variable) {
    control_system_.reject_configuration(variable);
}

bool EdgeRuntime::advance_recipe_phase() {
    const auto current_index =
        active_phase_index(elapsed_recipe_hours());
    const auto& phases = control_system_.recipe().phases;
    if (current_index + 1 >= phases.size()) {
        return false;
    }

    double next_phase_start_hours = 0.0;
    for (std::size_t index = 0; index <= current_index; ++index) {
        next_phase_start_hours += phases[index].duration_hours;
    }
    const double unshifted_recipe_seconds =
        environment_->state().simulation_time_seconds -
        recipe_start_time_seconds_;
    recipe_time_offset_seconds_ =
        next_phase_start_hours * kSecondsPerHour -
        unshifted_recipe_seconds;

    const auto& previous_phase = phases[current_index].name;
    const auto& current_phase = phases[current_index + 1].name;
    history_phase_index_ = current_index + 1;
    cumulative_phase_dose_milliliters_.fill(0.0);
    reported_phase_index_ = current_index + 1;
    if (event_bus_) {
        event_bus_->publish(
            RecipePhaseChanged{
                zone_id_,
                environment_->state().simulation_time_seconds,
                previous_phase,
                current_phase,
            });
    }
    return true;
}

}  // namespace smarthydro
