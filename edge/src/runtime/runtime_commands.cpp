#include <smarthydro/runtime/runtime_commands.hpp>

#include <exception>
#include <stdexcept>
#include <type_traits>
#include <utility>

namespace smarthydro {
namespace {

RuntimeCommandResult succeeded(
    const RuntimeCommandEnvelope& envelope,
    std::string message) {
    return {
        envelope.command_id,
        runtime_command_type(envelope.command),
        RuntimeCommandStatus::SUCCEEDED,
        false,
        std::move(message),
    };
}

RuntimeCommandResult rejected(
    const RuntimeCommandEnvelope& envelope,
    std::string message) {
    return {
        envelope.command_id,
        runtime_command_type(envelope.command),
        RuntimeCommandStatus::REJECTED,
        false,
        std::move(message),
    };
}

}  // namespace

const char* runtime_command_type(const RuntimeCommand& command) noexcept {
    return std::visit(
        [](const auto& value) noexcept -> const char* {
            using Command = std::decay_t<decltype(value)>;
            if constexpr (std::is_same_v<Command, ChangeStrategyCommand>) {
                return "ChangeStrategy";
            } else if constexpr (std::is_same_v<Command, LoadRecipeCommand>) {
                return "LoadRecipe";
            } else if constexpr (
                std::is_same_v<Command, ActivateCultivationCommand>) {
                return "ActivateCultivation";
            } else if constexpr (
                std::is_same_v<Command, ConfirmConfigurationCommand>) {
                return "ConfirmConfiguration";
            } else if constexpr (
                std::is_same_v<Command, RejectConfigurationCommand>) {
                return "RejectConfiguration";
            } else if constexpr (std::is_same_v<Command, InjectFaultCommand>) {
                return "InjectFault";
            } else if constexpr (std::is_same_v<Command, ResetFaultCommand>) {
                return "ResetFault";
            } else if constexpr (
                std::is_same_v<Command, AdvanceRecipePhaseCommand>) {
                return "AdvanceRecipePhase";
            } else if constexpr (
                std::is_same_v<Command, EmergencyStopCommand>) {
                return "EmergencyStop";
            } else {
                return "ResetEmergency";
            }
        },
        command);
}

const char* to_string(RuntimeCommandStatus status) noexcept {
    switch (status) {
        case RuntimeCommandStatus::SUCCEEDED:
            return "Succeeded";
        case RuntimeCommandStatus::REJECTED:
            return "Rejected";
    }
    return "Unknown";
}

RuntimeCommandProcessor::RuntimeCommandProcessor(EdgeRuntime& runtime)
    : runtime_(runtime) {}

RuntimeCommandResult RuntimeCommandProcessor::execute(
    const RuntimeCommandEnvelope& envelope) {
    if (envelope.command_id.empty()) {
        return rejected(envelope, "command_id must not be empty");
    }

    std::lock_guard<std::mutex> lock(mutex_);
    const auto previous = results_.find(envelope.command_id);
    if (previous != results_.end()) {
        auto replay = previous->second;
        replay.replayed = true;
        return replay;
    }

    auto result = execute_once(envelope);
    results_.emplace(envelope.command_id, result);
    return result;
}

std::size_t RuntimeCommandProcessor::processed_command_count() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return results_.size();
}

RuntimeCommandResult RuntimeCommandProcessor::execute_once(
    const RuntimeCommandEnvelope& envelope) noexcept {
    try {
        return std::visit(
            [this, &envelope](const auto& command) {
                using Command = std::decay_t<decltype(command)>;
                if constexpr (
                    std::is_same_v<Command, ChangeStrategyCommand>) {
                    runtime_.change_strategy(
                        command.variable,
                        command.strategy,
                        command.parameters);
                    return succeeded(envelope, "strategy changed");
                } else if constexpr (
                    std::is_same_v<Command, LoadRecipeCommand>) {
                    runtime_.replace_recipe(command.recipe);
                    return succeeded(envelope, "recipe loaded");
                } else if constexpr (
                    std::is_same_v<Command, ActivateCultivationCommand>) {
                    return rejected(
                        envelope,
                        "ActivateCultivation must be handled by the zone controller");
                } else if constexpr (
                    std::is_same_v<Command, ConfirmConfigurationCommand>) {
                    const auto confirmation =
                        runtime_.confirm_configuration(command.variable);
                    if (!confirmation.success) {
                        return rejected(envelope, confirmation.error);
                    }
                    return succeeded(envelope, "configuration confirmed");
                } else if constexpr (
                    std::is_same_v<Command, RejectConfigurationCommand>) {
                    runtime_.reject_configuration(command.variable);
                    return succeeded(envelope, "configuration rejected");
                } else if constexpr (
                    std::is_same_v<Command, InjectFaultCommand>) {
                    runtime_.inject_fault(
                        command.fault_id,
                        command.severity,
                        command.diagnostic);
                    return succeeded(envelope, "fault injected");
                } else if constexpr (
                    std::is_same_v<Command, ResetFaultCommand>) {
                    if (!runtime_.reset_injected_fault(command.fault_id)) {
                        return rejected(envelope, "injected fault not found");
                    }
                    return succeeded(envelope, "fault reset");
                } else if constexpr (
                    std::is_same_v<Command, AdvanceRecipePhaseCommand>) {
                    if (!runtime_.advance_recipe_phase()) {
                        return rejected(
                            envelope,
                            "recipe is already in its last phase");
                    }
                    return succeeded(envelope, "recipe phase advanced");
                } else if constexpr (
                    std::is_same_v<Command, EmergencyStopCommand>) {
                    const bool transitioned =
                        runtime_.trigger_emergency_stop(command.reason);
                    return succeeded(
                        envelope,
                        transitioned
                            ? "emergency stop applied"
                            : "emergency stop already active");
                } else {
                    if (!runtime_.request_manual_reset()) {
                        return rejected(
                            envelope,
                            "runtime is not in EmergencyLockdown");
                    }
                    return succeeded(envelope, "emergency reset requested");
                }
            },
            envelope.command);
    } catch (const std::exception& error) {
        return rejected(envelope, error.what());
    } catch (...) {
        return rejected(envelope, "unknown command execution failure");
    }
}

}  // namespace smarthydro
