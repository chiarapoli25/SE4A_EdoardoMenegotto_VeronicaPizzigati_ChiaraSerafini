#include <smarthydro/backend/http_backend_client.hpp>

#include <smarthydro/recipes/recipe_json.hpp>

#include <curl/curl.h>
#include <nlohmann/json.hpp>

#include <algorithm>
#include <atomic>
#include <cmath>
#include <condition_variable>
#include <cstdint>
#include <deque>
#include <fstream>
#include <iomanip>
#include <mutex>
#include <random>
#include <set>
#include <sstream>
#include <stdexcept>
#include <thread>
#include <type_traits>
#include <utility>

namespace smarthydro {
namespace {

using Json = nlohmann::json;

std::string generated_identifier() {
    static std::atomic<std::uint64_t> counter{0};
    static thread_local std::mt19937_64 generator(std::random_device{}());
    std::ostringstream output;
    output << std::hex << std::setfill('0')
           << std::setw(16) << generator()
           << std::setw(16) << (
                  static_cast<std::uint64_t>(
                      std::chrono::steady_clock::now()
                          .time_since_epoch()
                          .count()) ^
                  counter.fetch_add(1));
    return output.str();
}

std::string utc_now() {
    const auto now = std::chrono::system_clock::now();
    const auto time = std::chrono::system_clock::to_time_t(now);
    std::tm utc{};
#if defined(_WIN32)
    gmtime_s(&utc, &time);
#else
    gmtime_r(&time, &utc);
#endif
    std::ostringstream output;
    output << std::put_time(&utc, "%Y-%m-%dT%H:%M:%SZ");
    return output.str();
}

template <typename T>
Json optional_json(const std::optional<T>& value) {
    return value.has_value() ? Json(*value) : Json(nullptr);
}

Json fertilizer_bools(const FertilizerValues<bool>& values) {
    Json result = Json::object();
    for (std::size_t index = 0; index < kFertilizerTypeCount; ++index) {
        result[to_string(static_cast<FertilizerType>(index))] = values[index];
    }
    return result;
}

Json fertilizer_numbers(const FertilizerValues<double>& values) {
    Json result = Json::object();
    for (std::size_t index = 0; index < kFertilizerTypeCount; ++index) {
        result[to_string(static_cast<FertilizerType>(index))] = values[index];
    }
    return result;
}

Json actuator_command_json(const ActuatorCommand& command) {
    return {
        {"requested_irrigation_volume_liters",
         command.requested_irrigation_volume_liters},
        {"fertilizer_valves_open",
         fertilizer_bools(command.fertilizer_valves_open)},
        {"lighting_percent", command.lighting_percent},
    };
}

Json actuator_output_json(const ActuatorOutput& output) {
    return {
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
    };
}

const char* severity_name(ControlFaultSeverity severity) noexcept {
    switch (severity) {
        case ControlFaultSeverity::NONE:
            return "None";
        case ControlFaultSeverity::RECOVERABLE:
            return "Recoverable";
        case ControlFaultSeverity::CRITICAL:
            return "Critical";
    }
    return "Unknown";
}

struct PendingUpload {
    std::string message_id;
    std::string path;
    std::string body;
    std::size_t attempts = 0;
    std::chrono::steady_clock::time_point next_attempt =
        std::chrono::steady_clock::now();
    bool persisted = false;
};

PendingUpload event_upload(
    const std::string& edge_id,
    const std::string& boot_id,
    const std::string& zone_id,
    const char* event_type,
    double timestamp_seconds,
    Json payload) {
    const auto event_id = generated_identifier();
    Json body = {
        {"event_id", event_id},
        {"edge_id", edge_id},
        {"boot_id", boot_id},
        {"event_type", event_type},
        {"timestamp_seconds", timestamp_seconds},
        {"recorded_at", utc_now()},
        {"payload", std::move(payload)},
    };
    return {
        event_id,
        "/api/v1/zones/" + zone_id + "/events",
        body.dump(),
    };
}

std::vector<PendingUpload> uploads_from_event(
    const EdgeDomainEvent& event,
    const HttpBackendConfig& config) {
    return std::visit(
        [&config](const auto& value) -> std::vector<PendingUpload> {
            using Event = std::decay_t<decltype(value)>;
            if constexpr (std::is_same_v<Event, BackendUnavailable>) {
                return {};
            } else if constexpr (std::is_same_v<Event, TelemetrySample>) {
                const auto recorded_at = utc_now();
                const auto telemetry_id = generated_identifier();
                const auto actuator_id = generated_identifier();
                Json telemetry = {
                    {"boot_id", config.boot_id},
                    {"sequence_number", value.sequence_number},
                    {"timestamp_seconds", value.timestamp_seconds},
                    {"recorded_at", recorded_at},
                    {"temperature_c",
                     optional_json(value.readings.temperature_c)},
                    {"air_humidity_percent",
                     optional_json(value.readings.air_humidity_percent)},
                    {"soil_moisture_percent",
                     optional_json(value.readings.soil_moisture_percent)},
                    {"ph", optional_json(value.readings.ph)},
                    {"light_ppfd_umol_m2_s",
                     optional_json(
                         value.readings.light_ppfd_umol_m2_s)},
                };
                Json actuators = {
                    {"boot_id", config.boot_id},
                    {"sequence_number", value.sequence_number},
                    {"timestamp_seconds", value.timestamp_seconds},
                    {"recorded_at", recorded_at},
                    {"command",
                     actuator_command_json(value.actuator_command)},
                    {"output",
                     actuator_output_json(value.actuator_output)},
                };
                return {
                    {
                        telemetry_id,
                        "/api/v1/zones/" + value.zone_id + "/telemetry",
                        telemetry.dump(),
                    },
                    {
                        actuator_id,
                        "/api/v1/zones/" + value.zone_id + "/actuators",
                        actuators.dump(),
                    },
                };
            } else {
                Json payload = Json::object();
                if constexpr (
                    std::is_same_v<Event, ZoneLifecycleChanged>) {
                    payload = {
                        {"previous_state", value.previous_state},
                        {"current_state", value.current_state},
                        {"reason", value.reason},
                    };
                } else if constexpr (std::is_same_v<Event, StateChanged>) {
                    payload = {
                        {"previous_state", to_string(value.previous_state)},
                        {"current_state", to_string(value.current_state)},
                        {"reason", value.reason},
                    };
                } else if constexpr (std::is_same_v<Event, FaultDetected>) {
                    payload = {
                        {"fault_type", value.fault_type},
                        {"severity", severity_name(value.severity)},
                        {"diagnostic", value.diagnostic},
                    };
                } else if constexpr (std::is_same_v<Event, StrategyChanged>) {
                    payload = {
                        {"variable", to_string(value.variable)},
                        {"previous_strategy",
                         to_string(value.previous_strategy)},
                        {"current_strategy",
                         to_string(value.current_strategy)},
                    };
                } else if constexpr (
                    std::is_same_v<Event, RecipePhaseChanged>) {
                    payload = {
                        {"previous_phase", value.previous_phase},
                        {"current_phase", value.current_phase},
                    };
                } else if constexpr (
                    std::is_same_v<Event, EmergencyTriggered>) {
                    payload = {{"reason", value.reason}};
                } else if constexpr (
                    std::is_same_v<Event, CommandExecuted>) {
                    payload = {
                        {"command", actuator_command_json(value.command)},
                        {"output", actuator_output_json(value.output)},
                        {"delivered_water_liters",
                         value.delivered_water_liters},
                        {"delivered_fertilizer_milliliters",
                         fertilizer_numbers(
                             value.delivered_fertilizer_milliliters)},
                    };
                } else if constexpr (
                    std::is_same_v<Event, CommandFailed>) {
                    payload = {
                        {"actuator", value.actuator},
                        {"diagnostic", value.diagnostic},
                    };
                }
                return {
                    event_upload(
                        config.edge_id,
                        config.boot_id,
                        value.zone_id,
                        event_type_name(EdgeDomainEvent{value}),
                        value.timestamp_seconds,
                        std::move(payload)),
                };
            }
        },
        event);
}

ControlledVariable variable_from_string(const std::string& value) {
    for (std::size_t index = 0; index < kControlledVariableCount; ++index) {
        const auto variable = static_cast<ControlledVariable>(index);
        if (value == to_string(variable)) {
            return variable;
        }
    }
    throw std::invalid_argument("unknown controlled variable: " + value);
}

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
    throw std::invalid_argument("unknown strategy: " + value);
}

ControlDirection direction_from_string(const std::string& value) {
    if (value == "increases" || value == "IncreasesProcessValue") {
        return ControlDirection::INCREASES_PROCESS_VALUE;
    }
    if (value == "decreases" || value == "DecreasesProcessValue") {
        return ControlDirection::DECREASES_PROCESS_VALUE;
    }
    throw std::invalid_argument("unknown control direction: " + value);
}

ControllerParameters controller_parameters_from_json(
    StrategyType strategy,
    const Json& json) {
    if (strategy == StrategyType::THRESHOLD) {
        return ThresholdConfig{
            json.at("lower_threshold").get<double>(),
            json.at("upper_threshold").get<double>(),
            direction_from_string(json.at("direction").get<std::string>()),
            json.at("active_command").get<double>(),
            json.at("inactive_command").get<double>(),
            json.value("bidirectional", false),
        };
    }
    if (strategy == StrategyType::PID) {
        return PidConfig{
            json.at("setpoint").get<double>(),
            json.at("proportional_gain").get<double>(),
            json.at("integral_gain").get<double>(),
            json.at("derivative_gain").get<double>(),
            {
                json.at("command_minimum").get<double>(),
                json.at("command_maximum").get<double>(),
            },
            direction_from_string(json.at("direction").get<std::string>()),
        };
    }
    return PredictiveConfig{
        json.at("setpoint").get<double>(),
        json.at("prediction_horizon_steps").get<double>(),
        json.at("response_gain").get<double>(),
        json.at("neutral_command").get<double>(),
        {
            json.at("command_minimum").get<double>(),
            json.at("command_maximum").get<double>(),
        },
        direction_from_string(json.at("direction").get<std::string>()),
        json.value("water_dilution_gain", 0.0),
        json.value("cumulative_dose_gain", 0.0),
        json.value("substrate_gain", 0.0),
    };
}

std::size_t curl_write(
    char* data,
    std::size_t size,
    std::size_t count,
    void* target) {
    const auto bytes = size * count;
    static_cast<std::string*>(target)->append(data, bytes);
    return bytes;
}

void require_curl_initialized() {
    static const int initialized = [] {
        const auto result = curl_global_init(CURL_GLOBAL_DEFAULT);
        if (result != CURLE_OK) {
            throw std::runtime_error("cannot initialize libcurl");
        }
        return 1;
    }();
    static_cast<void>(initialized);
}

}  // namespace

CurlHttpTransport::CurlHttpTransport(
    std::string base_url,
    std::chrono::milliseconds connect_timeout,
    std::chrono::milliseconds request_timeout,
    std::string bearer_token)
    : base_url_(std::move(base_url)),
      connect_timeout_(connect_timeout),
      request_timeout_(request_timeout),
      bearer_token_(std::move(bearer_token)) {
    if (base_url_.empty()) {
        throw std::invalid_argument("backend base URL must not be empty");
    }
    while (!base_url_.empty() && base_url_.back() == '/') {
        base_url_.pop_back();
    }
    require_curl_initialized();
}

HttpResponse CurlHttpTransport::get(const std::string& path) {
    return request("GET", path, {});
}

HttpResponse CurlHttpTransport::post(
    const std::string& path,
    const std::string& json_body) {
    return request("POST", path, json_body);
}

HttpResponse CurlHttpTransport::request(
    const std::string& method,
    const std::string& path,
    const std::string& json_body) {
    auto* curl = curl_easy_init();
    if (!curl) {
        throw std::runtime_error("cannot create libcurl request");
    }
    HttpResponse response;
    curl_slist* headers = nullptr;
    headers = curl_slist_append(headers, "Content-Type: application/json");
    std::string authorization_header;
    if (!bearer_token_.empty()) {
        authorization_header = "Authorization: Bearer " + bearer_token_;
        headers =
            curl_slist_append(headers, authorization_header.c_str());
    }
    const auto url = base_url_ + path;
    curl_easy_setopt(curl, CURLOPT_URL, url.c_str());
    curl_easy_setopt(curl, CURLOPT_HTTPHEADER, headers);
    curl_easy_setopt(curl, CURLOPT_CONNECTTIMEOUT_MS,
                     static_cast<long>(connect_timeout_.count()));
    curl_easy_setopt(curl, CURLOPT_TIMEOUT_MS,
                     static_cast<long>(request_timeout_.count()));
    curl_easy_setopt(curl, CURLOPT_NOSIGNAL, 1L);
    curl_easy_setopt(curl, CURLOPT_WRITEFUNCTION, curl_write);
    curl_easy_setopt(curl, CURLOPT_WRITEDATA, &response.body);
    if (method == "POST") {
        curl_easy_setopt(curl, CURLOPT_POST, 1L);
        curl_easy_setopt(curl, CURLOPT_POSTFIELDS, json_body.c_str());
        curl_easy_setopt(
            curl,
            CURLOPT_POSTFIELDSIZE,
            static_cast<long>(json_body.size()));
    }
    const auto result = curl_easy_perform(curl);
    if (result == CURLE_OK) {
        long status = 0;
        curl_easy_getinfo(curl, CURLINFO_RESPONSE_CODE, &status);
        response.status_code = static_cast<int>(status);
    } else {
        response.status_code = 0;
        response.body = curl_easy_strerror(result);
    }
    curl_slist_free_all(headers);
    curl_easy_cleanup(curl);
    return response;
}

RuntimeCommandEnvelope runtime_command_from_json(
    const std::string& json_text) {
    try {
        const auto json = Json::parse(json_text);
        const auto command_id = json.at("command_id").get<std::string>();
        const auto command_type = json.at("command_type").get<std::string>();
        const auto payload = json.value("payload", Json::object());
        if (command_type == "ChangeStrategy") {
            const auto strategy =
                strategy_from_string(payload.at("strategy").get<std::string>());
            return {
                command_id,
                ChangeStrategyCommand{
                    variable_from_string(
                        payload.at("variable").get<std::string>()),
                    strategy,
                    controller_parameters_from_json(
                        strategy,
                        payload.at("parameters")),
                },
            };
        }
        if (command_type == "LoadRecipe") {
            const auto& recipe_json =
                payload.contains("recipe") ? payload.at("recipe") : payload;
            return {
                command_id,
                LoadRecipeCommand{recipe_from_json(recipe_json.dump())},
            };
        }
        if (command_type == "ActivateCultivation") {
            const auto& recipe_json =
                payload.contains("recipe") ? payload.at("recipe") : payload;
            return {
                command_id,
                ActivateCultivationCommand{
                    payload.at("cultivation_id").get<std::string>(),
                    recipe_from_json(recipe_json.dump()),
                },
            };
        }
        if (command_type == "PauseCultivation") {
            return {command_id, PauseCultivationCommand{}};
        }
        if (command_type == "ResumeCultivation") {
            return {command_id, ResumeCultivationCommand{}};
        }
        if (command_type == "StopCultivation") {
            return {command_id, StopCultivationCommand{}};
        }
        if (command_type == "ConfirmConfiguration") {
            return {
                command_id,
                ConfirmConfigurationCommand{
                    variable_from_string(
                        payload.at("variable").get<std::string>()),
                },
            };
        }
        if (command_type == "RejectConfiguration") {
            return {
                command_id,
                RejectConfigurationCommand{
                    variable_from_string(
                        payload.at("variable").get<std::string>()),
                },
            };
        }
        if (command_type == "InjectFault") {
            const auto severity_text =
                payload.at("severity").get<std::string>();
            const auto severity =
                severity_text == "Critical"
                    ? ControlFaultSeverity::CRITICAL
                    : ControlFaultSeverity::RECOVERABLE;
            return {
                command_id,
                InjectFaultCommand{
                    payload.at("fault_id").get<std::string>(),
                    severity,
                    payload.at("diagnostic").get<std::string>(),
                },
            };
        }
        if (command_type == "ResetFault") {
            return {
                command_id,
                ResetFaultCommand{
                    payload.at("fault_id").get<std::string>(),
                },
            };
        }
        if (command_type == "AdvanceRecipePhase") {
            return {command_id, AdvanceRecipePhaseCommand{}};
        }
        if (command_type == "EmergencyStop") {
            return {
                command_id,
                EmergencyStopCommand{
                    payload.value(
                        "reason",
                        "runtime emergency stop requested"),
                },
            };
        }
        if (command_type == "ResetEmergency") {
            return {command_id, ResetEmergencyCommand{}};
        }
        throw std::invalid_argument(
            "unknown runtime command type: " + command_type);
    } catch (const std::exception& error) {
        throw std::invalid_argument(
            "invalid runtime command JSON: " +
            std::string(error.what()));
    }
}

class HttpBackendClient::Impl {
public:
    Impl(
        EventBus& event_bus,
        std::vector<std::string> zone_ids,
        HttpBackendConfig config,
        std::shared_ptr<IHttpTransport> transport)
        : event_bus_(event_bus),
          zone_ids_(std::move(zone_ids)),
          config_(std::move(config)),
          transport_(
              transport
                  ? std::move(transport)
                  : std::make_shared<CurlHttpTransport>(
                        config_.base_url,
                        config_.connect_timeout,
                        config_.request_timeout,
                        config_.bearer_token)) {
        if (zone_ids_.empty() || config_.edge_id.empty()) {
            throw std::invalid_argument(
                "HTTP backend client requires edge and zone identifiers");
        }
        if (config_.boot_id.empty()) {
            config_.boot_id = generated_identifier();
        }
        std::filesystem::create_directories(config_.outbox_directory);
        load_outbox();
    }

    ~Impl() {
        stop();
    }

    void start() {
        std::lock_guard<std::mutex> lock(mutex_);
        if (running_) {
            return;
        }
        stopping_ = false;
        running_ = true;
        worker_ = std::thread([this] { worker_loop(); });
    }

    void stop() noexcept {
        {
            std::lock_guard<std::mutex> lock(mutex_);
            if (!running_) {
                return;
            }
            stopping_ = true;
        }
        condition_.notify_all();
        if (worker_.joinable()) {
            worker_.join();
        }
        std::lock_guard<std::mutex> lock(mutex_);
        running_ = false;
    }

    void enqueue_event(const EdgeDomainEvent& event) {
        auto new_uploads = uploads_from_event(event, config_);
        if (new_uploads.empty()) {
            return;
        }
        {
            std::lock_guard<std::mutex> lock(mutex_);
            for (auto& upload : new_uploads) {
                uploads_.push_back(std::move(upload));
            }
        }
        condition_.notify_all();
    }

    std::vector<RemoteRuntimeCommand> take_commands() {
        std::lock_guard<std::mutex> lock(mutex_);
        std::vector<RemoteRuntimeCommand> result;
        result.reserve(commands_.size());
        while (!commands_.empty()) {
            result.push_back(std::move(commands_.front()));
            commands_.pop_front();
        }
        return result;
    }

    void submit_result(
        const std::string& zone_id,
        const RuntimeCommandResult& result) {
        Json body = {
            {"status", result.success() ? "succeeded" : "rejected"},
            {"message", result.message},
            {"replayed", result.replayed},
        };
        PendingUpload upload{
            generated_identifier(),
            "/api/v1/zones/" + zone_id + "/commands/" +
                result.command_id + "/result",
            body.dump(),
        };
        {
            std::lock_guard<std::mutex> lock(mutex_);
            uploads_.push_back(std::move(upload));
        }
        condition_.notify_all();
    }

    std::size_t pending_count() const {
        std::lock_guard<std::mutex> lock(mutex_);
        return uploads_.size() + in_flight_uploads_;
    }

    const std::string& boot_id() const noexcept {
        return config_.boot_id;
    }

private:
    std::filesystem::path outbox_path(
        const PendingUpload& upload) const {
        return config_.outbox_directory /
               (upload.message_id + ".json");
    }

    void persist(PendingUpload& upload) {
        if (upload.persisted) {
            return;
        }
        const auto final_path = outbox_path(upload);
        const auto temporary_path =
            final_path.string() + ".tmp";
        Json stored = {
            {"message_id", upload.message_id},
            {"path", upload.path},
            {"body", upload.body},
            {"attempts", upload.attempts},
        };
        {
            std::ofstream output(
                temporary_path,
                std::ios::out | std::ios::trunc);
            if (!output) {
                throw std::runtime_error(
                    "cannot write backend outbox message");
            }
            output << stored.dump(2) << '\n';
        }
        std::filesystem::rename(temporary_path, final_path);
        upload.persisted = true;
    }

    void load_outbox() {
        for (const auto& entry :
             std::filesystem::directory_iterator(
                 config_.outbox_directory)) {
            if (!entry.is_regular_file() ||
                entry.path().extension() != ".json") {
                continue;
            }
            std::ifstream input(entry.path());
            Json stored;
            input >> stored;
            uploads_.push_back(
                {
                    stored.at("message_id").get<std::string>(),
                    stored.at("path").get<std::string>(),
                    stored.at("body").get<std::string>(),
                    stored.value("attempts", 0U),
                    std::chrono::steady_clock::now(),
                    true,
                });
        }
    }

    std::chrono::milliseconds retry_delay(
        std::size_t attempts) const {
        const auto exponent = std::min<std::size_t>(attempts, 10);
        const auto multiplier =
            static_cast<std::int64_t>(1) << exponent;
        const auto proposed =
            config_.retry_base_delay * multiplier;
        return std::min(proposed, config_.retry_max_delay);
    }

    void report_unavailable(
        const PendingUpload& upload,
        const std::string& diagnostic) {
        if (outage_reported_) {
            return;
        }
        outage_reported_ = true;
        std::string zone_id = "unknown";
        const std::string marker = "/zones/";
        const auto start = upload.path.find(marker);
        if (start != std::string::npos) {
            const auto id_start = start + marker.size();
            const auto id_end = upload.path.find('/', id_start);
            zone_id = upload.path.substr(id_start, id_end - id_start);
        }
        event_bus_.publish(
            BackendUnavailable{
                zone_id,
                0.0,
                config_.base_url + upload.path,
                diagnostic,
            });
    }

    void poll_commands() {
        for (const auto& zone_id : zone_ids_) {
            const auto response = transport_->get(
                "/api/v1/zones/" + zone_id + "/commands?limit=100");
            if (!response.successful()) {
                PendingUpload diagnostic{
                    "poll",
                    "/api/v1/zones/" + zone_id + "/commands",
                    {},
                };
                report_unavailable(
                    diagnostic,
                    response.body.empty()
                        ? "command polling failed"
                        : response.body);
                continue;
            }
            outage_reported_ = false;
            const auto commands = Json::parse(response.body);
            for (const auto& command : commands) {
                const auto command_id =
                    command.at("command_id").get<std::string>();
                {
                    std::lock_guard<std::mutex> lock(mutex_);
                    if (known_command_ids_.count(command_id) != 0) {
                        continue;
                    }
                }
                try {
                    auto prepared_command = command;
                    const auto prepared_type =
                        prepared_command.at("command_type")
                            .get<std::string>();
                    if (
                        (prepared_type == "LoadRecipe" ||
                         prepared_type == "ActivateCultivation") &&
                        prepared_command.at("payload").contains("recipe_id") &&
                        !prepared_command.at("payload").contains("recipe")) {
                        const auto recipe_id =
                            prepared_command.at("payload")
                                .at("recipe_id")
                                .get<std::string>();
                        if (
                            recipe_id.empty() ||
                            recipe_id.find('/') != std::string::npos ||
                            recipe_id.find('\\') != std::string::npos) {
                            throw std::invalid_argument(
                                "invalid recipe identifier");
                        }
                        const auto recipe_response = transport_->get(
                            "/api/v1/recipes/" + recipe_id);
                        if (recipe_response.status_code == 404) {
                            throw std::invalid_argument(
                                "remote recipe not found: " + recipe_id);
                        }
                        if (!recipe_response.successful()) {
                            PendingUpload diagnostic{
                                "recipe",
                                "/api/v1/recipes/" + recipe_id,
                                {},
                            };
                            report_unavailable(
                                diagnostic,
                                recipe_response.body.empty()
                                    ? "recipe download failed"
                                    : recipe_response.body);
                            continue;
                        }
                        prepared_command["payload"]["recipe"] =
                            Json::parse(recipe_response.body);
                    }
                    auto envelope =
                        runtime_command_from_json(prepared_command.dump());
                    std::lock_guard<std::mutex> lock(mutex_);
                    if (!known_command_ids_.insert(command_id).second) {
                        continue;
                    }
                    commands_.push_back(
                        {zone_id, std::move(envelope)});
                } catch (const std::exception& error) {
                    {
                        std::lock_guard<std::mutex> lock(mutex_);
                        if (!known_command_ids_.insert(command_id).second) {
                            continue;
                        }
                    }
                    RuntimeCommandResult rejected{
                        command_id,
                        command.value("command_type", "Unknown"),
                        RuntimeCommandStatus::REJECTED,
                        false,
                        error.what(),
                    };
                    submit_result(zone_id, rejected);
                }
            }
        }
    }

    void persist_remaining() noexcept {
        std::deque<PendingUpload> pending;
        {
            std::lock_guard<std::mutex> lock(mutex_);
            pending = uploads_;
        }
        for (auto& upload : pending) {
            try {
                persist(upload);
            } catch (...) {
                // Lo shutdown non puo propagare eccezioni.
            }
        }
    }

    void worker_loop() {
        auto next_poll = std::chrono::steady_clock::now();
        while (true) {
            PendingUpload upload;
            bool has_upload = false;
            {
                std::unique_lock<std::mutex> lock(mutex_);
                condition_.wait_for(
                    lock,
                    std::chrono::milliseconds(25),
                    [this] { return stopping_ || !uploads_.empty(); });
                if (stopping_) {
                    break;
                }
                const auto now = std::chrono::steady_clock::now();
                const auto iterator = std::find_if(
                    uploads_.begin(),
                    uploads_.end(),
                    [now](const PendingUpload& candidate) {
                        return candidate.next_attempt <= now;
                    });
                if (iterator != uploads_.end()) {
                    upload = std::move(*iterator);
                    uploads_.erase(iterator);
                    ++in_flight_uploads_;
                    has_upload = true;
                }
            }

            if (has_upload) {
                bool requeue = false;
                try {
                    persist(upload);
                    const auto response =
                        transport_->post(upload.path, upload.body);
                    if (response.successful()) {
                        std::error_code ignored;
                        std::filesystem::remove(
                            outbox_path(upload),
                            ignored);
                        outage_reported_ = false;
                    } else {
                        requeue = true;
                        ++upload.attempts;
                        upload.next_attempt =
                            std::chrono::steady_clock::now() +
                            retry_delay(upload.attempts);
                        report_unavailable(
                            upload,
                            response.body.empty()
                                ? "backend rejected the upload"
                                : response.body);
                    }
                } catch (const std::exception& error) {
                    requeue = true;
                    ++upload.attempts;
                    upload.next_attempt =
                        std::chrono::steady_clock::now() +
                        retry_delay(upload.attempts);
                    report_unavailable(upload, error.what());
                }
                {
                    std::lock_guard<std::mutex> lock(mutex_);
                    --in_flight_uploads_;
                    if (requeue) {
                        uploads_.push_back(std::move(upload));
                    }
                }
                condition_.notify_all();
            }

            const auto now = std::chrono::steady_clock::now();
            if (now >= next_poll) {
                try {
                    poll_commands();
                } catch (const std::exception& error) {
                    PendingUpload diagnostic{
                        "poll",
                        "/api/v1/commands",
                        {},
                    };
                    report_unavailable(diagnostic, error.what());
                }
                next_poll = now + config_.command_poll_interval;
            }
        }
        persist_remaining();
    }

    EventBus& event_bus_;
    std::vector<std::string> zone_ids_;
    HttpBackendConfig config_;
    std::shared_ptr<IHttpTransport> transport_;
    mutable std::mutex mutex_;
    std::condition_variable condition_;
    std::deque<PendingUpload> uploads_;
    std::size_t in_flight_uploads_ = 0;
    std::deque<RemoteRuntimeCommand> commands_;
    std::set<std::string> known_command_ids_;
    std::thread worker_;
    bool running_ = false;
    bool stopping_ = false;
    bool outage_reported_ = false;
};

HttpBackendClient::HttpBackendClient(
    EventBus& event_bus,
    std::vector<std::string> zone_ids,
    HttpBackendConfig config,
    std::shared_ptr<IHttpTransport> transport)
    : impl_(std::make_unique<Impl>(
          event_bus,
          std::move(zone_ids),
          std::move(config),
          std::move(transport))) {}

HttpBackendClient::~HttpBackendClient() = default;

void HttpBackendClient::start() {
    impl_->start();
}

void HttpBackendClient::stop() noexcept {
    impl_->stop();
}

void HttpBackendClient::on_event(const EdgeDomainEvent& event) {
    impl_->enqueue_event(event);
}

std::vector<RemoteRuntimeCommand>
HttpBackendClient::take_commands() {
    return impl_->take_commands();
}

void HttpBackendClient::submit_command_result(
    const std::string& zone_id,
    const RuntimeCommandResult& result) {
    impl_->submit_result(zone_id, result);
}

bool HttpBackendClient::flush(std::chrono::milliseconds timeout) {
    const auto deadline = std::chrono::steady_clock::now() + timeout;
    while (std::chrono::steady_clock::now() < deadline) {
        if (impl_->pending_count() == 0) {
            return true;
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(10));
    }
    return impl_->pending_count() == 0;
}

std::size_t HttpBackendClient::pending_upload_count() const {
    return impl_->pending_count();
}

const std::string& HttpBackendClient::boot_id() const noexcept {
    return impl_->boot_id();
}

}  // namespace smarthydro
