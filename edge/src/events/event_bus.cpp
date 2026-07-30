#include <smarthydro/events/event_bus.hpp>

#include <iomanip>
#include <sstream>
#include <stdexcept>
#include <type_traits>
#include <utility>
#include <vector>

namespace smarthydro {
namespace {

const char* state_name(OperationalState state) noexcept {
    switch (state) {
        case OperationalState::NOMINAL:
            return "Nominal";
        case OperationalState::DEGRADED:
            return "Degraded";
        case OperationalState::EMERGENCY_LOCKDOWN:
            return "EmergencyLockdown";
    }
    return "Unknown";
}

std::string csv_escape(const std::string& value) {
    if (value.find_first_of(",\"\n") == std::string::npos) {
        return value;
    }
    std::string escaped = "\"";
    for (const char character : value) {
        if (character == '"') {
            escaped += "\"\"";
        } else {
            escaped += character;
        }
    }
    escaped += '"';
    return escaped;
}

std::string optional_number(const std::optional<double>& value) {
    if (!value.has_value()) {
        return {};
    }
    std::ostringstream output;
    output << std::setprecision(15) << *value;
    return output.str();
}

double event_timestamp(const EdgeDomainEvent& event) {
    return std::visit(
        [](const auto& value) {
            return value.timestamp_seconds;
        },
        event);
}

const std::string& event_zone(const EdgeDomainEvent& event) {
    return std::visit(
        [](const auto& value) -> const std::string& {
            return value.zone_id;
        },
        event);
}

std::string event_detail(const EdgeDomainEvent& event) {
    return std::visit(
        [](const auto& value) -> std::string {
            using Event = std::decay_t<decltype(value)>;
            if constexpr (std::is_same_v<Event, TelemetrySample>) {
                return "sequence=" +
                       std::to_string(value.sequence_number);
            } else if constexpr (
                std::is_same_v<Event, ZoneLifecycleChanged>) {
                return value.previous_state + " -> " +
                       value.current_state + ": " + value.reason;
            } else if constexpr (
                std::is_same_v<Event, SimulationSpeedChanged>) {
                return std::to_string(value.previous_time_scale) +
                       "x -> " +
                       std::to_string(value.current_time_scale) +
                       "x";
            } else if constexpr (
                std::is_same_v<Event, SchedulerLagStateChanged>) {
                return std::string(
                           value.lagging ? "lagging" : "recovered") +
                       " pending_steps=" +
                       std::to_string(value.pending_steps) +
                       " pending_seconds=" +
                       std::to_string(
                           value.pending_simulation_seconds);
            } else if constexpr (std::is_same_v<Event, StateChanged>) {
                return std::string(state_name(value.previous_state)) +
                       " -> " + state_name(value.current_state) +
                       ": " + value.reason;
            } else if constexpr (std::is_same_v<Event, FaultDetected>) {
                return value.fault_type + ": " + value.diagnostic;
            } else if constexpr (std::is_same_v<Event, StrategyChanged>) {
                return std::string(to_string(value.variable)) +
                       " strategy changed";
            } else if constexpr (
                std::is_same_v<Event, RecipePhaseChanged>) {
                return value.previous_phase + " -> " +
                       value.current_phase;
            } else if constexpr (
                std::is_same_v<Event, EmergencyTriggered>) {
                return value.reason;
            } else if constexpr (
                std::is_same_v<Event, BackendUnavailable>) {
                return value.endpoint + ": " + value.diagnostic;
            } else if constexpr (
                std::is_same_v<Event, CommandExecuted>) {
                return "water_delivered_liters=" +
                       std::to_string(
                           value.delivered_water_liters);
            } else {
                return value.actuator + ": " + value.diagnostic;
            }
        },
        event);
}

}  // namespace

const char* event_type_name(const EdgeDomainEvent& event) noexcept {
    return std::visit(
        [](const auto& value) noexcept {
            using Event = std::decay_t<decltype(value)>;
            if constexpr (std::is_same_v<Event, TelemetrySample>) {
                return "TelemetrySample";
            } else if constexpr (
                std::is_same_v<Event, ZoneLifecycleChanged>) {
                return "ZoneLifecycleChanged";
            } else if constexpr (
                std::is_same_v<Event, SimulationSpeedChanged>) {
                return "SimulationSpeedChanged";
            } else if constexpr (
                std::is_same_v<Event, SchedulerLagStateChanged>) {
                return "SchedulerLagStateChanged";
            } else if constexpr (std::is_same_v<Event, StateChanged>) {
                return "StateChanged";
            } else if constexpr (std::is_same_v<Event, FaultDetected>) {
                return "FaultDetected";
            } else if constexpr (std::is_same_v<Event, StrategyChanged>) {
                return "StrategyChanged";
            } else if constexpr (
                std::is_same_v<Event, RecipePhaseChanged>) {
                return "RecipePhaseChanged";
            } else if constexpr (
                std::is_same_v<Event, EmergencyTriggered>) {
                return "EmergencyTriggered";
            } else if constexpr (
                std::is_same_v<Event, BackendUnavailable>) {
                return "BackendUnavailable";
            } else if constexpr (
                std::is_same_v<Event, CommandExecuted>) {
                return "CommandExecuted";
            } else {
                return "CommandFailed";
            }
        },
        event);
}

EventBus::SubscriptionId EventBus::subscribe(
    std::shared_ptr<IEventObserver> observer) {
    if (!observer) {
        throw std::invalid_argument(
            "event bus requires a non-null observer");
    }
    std::lock_guard<std::mutex> lock(mutex_);
    const auto subscription_id = next_subscription_id_++;
    observers_.emplace(subscription_id, std::move(observer));
    return subscription_id;
}

void EventBus::unsubscribe(SubscriptionId subscription_id) noexcept {
    std::lock_guard<std::mutex> lock(mutex_);
    observers_.erase(subscription_id);
}

void EventBus::publish(const EdgeDomainEvent& event) noexcept {
    std::vector<std::shared_ptr<IEventObserver>> observers;
    {
        std::lock_guard<std::mutex> lock(mutex_);
        for (auto iterator = observers_.begin();
             iterator != observers_.end();) {
            if (auto observer = iterator->second.lock()) {
                observers.push_back(std::move(observer));
                ++iterator;
            } else {
                iterator = observers_.erase(iterator);
            }
        }
    }
    for (const auto& observer : observers) {
        try {
            observer->on_event(event);
        } catch (...) {
            // Un observer esterno non deve interrompere il controllo locale.
        }
    }
}

ConsoleLogger::ConsoleLogger(std::ostream& output)
    : output_(output) {}

void ConsoleLogger::on_event(const EdgeDomainEvent& event) {
    output_ << '[' << event_timestamp(event) << "] "
            << event_type_name(event)
            << " zone=" << event_zone(event)
            << " " << event_detail(event) << '\n';
}

CsvLogger::CsvLogger(std::ostream& output)
    : output_(&output) {
    write_header();
}

CsvLogger::CsvLogger(const std::string& file_path)
    : owned_output_(
          std::make_unique<std::ofstream>(
              file_path,
              std::ios::out | std::ios::trunc)),
      output_(owned_output_.get()) {
    if (!*owned_output_) {
        throw std::runtime_error(
            "cannot open event CSV file: " + file_path);
    }
    write_header();
}

void CsvLogger::write_header() {
    *output_
        << "timestamp,event_type,zone,state,temperature_c,"
           "air_humidity_percent,soil_moisture_percent,ph,"
           "light_ppfd,detail\n";
}

void CsvLogger::on_event(const EdgeDomainEvent& event) {
    *output_ << std::setprecision(15)
             << event_timestamp(event) << ','
             << event_type_name(event) << ','
             << csv_escape(event_zone(event)) << ',';
    if (const auto* telemetry =
            std::get_if<TelemetrySample>(&event)) {
        *output_
            << state_name(telemetry->operational_state) << ','
            << optional_number(telemetry->readings.temperature_c) << ','
            << optional_number(
                   telemetry->readings.air_humidity_percent) << ','
            << optional_number(
                   telemetry->readings.soil_moisture_percent) << ','
            << optional_number(telemetry->readings.ph) << ','
            << optional_number(
                   telemetry->readings.light_ppfd_umol_m2_s) << ',';
    } else {
        *output_ << ",,,,,,";
    }
    *output_ << csv_escape(event_detail(event)) << '\n';
}

BackendClient::BackendClient(
    EventBus& event_bus,
    std::string endpoint,
    SendFunction sender)
    : event_bus_(event_bus),
      endpoint_(std::move(endpoint)),
      sender_(std::move(sender)) {
    if (endpoint_.empty() || !sender_) {
        throw std::invalid_argument(
            "backend client requires endpoint and sender");
    }
}

void BackendClient::on_event(const EdgeDomainEvent& event) {
    if (std::holds_alternative<BackendUnavailable>(event) ||
        reporting_failure_) {
        return;
    }

    bool delivered = false;
    std::string diagnostic = "transport rejected the event";
    try {
        delivered = sender_(event);
    } catch (const std::exception& error) {
        diagnostic = error.what();
    } catch (...) {
        diagnostic = "unknown backend transport failure";
    }
    if (delivered) {
        return;
    }

    reporting_failure_ = true;
    event_bus_.publish(
        BackendUnavailable{
            event_zone(event),
            event_timestamp(event),
            endpoint_,
            diagnostic,
        });
    reporting_failure_ = false;
}

}  // namespace smarthydro
