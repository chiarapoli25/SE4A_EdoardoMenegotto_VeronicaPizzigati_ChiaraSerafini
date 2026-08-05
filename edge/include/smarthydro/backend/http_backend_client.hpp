#pragma once

/**
 * @file http_backend_client.hpp
 * @brief Client Edge asincrono con HTTP, outbox persistente e comandi remoti.
 */

#include <smarthydro/events/event_bus.hpp>
#include <smarthydro/runtime/runtime_commands.hpp>

#include <chrono>
#include <cstddef>
#include <filesystem>
#include <memory>
#include <string>
#include <vector>

namespace smarthydro {

/** Risposta minima indipendente dalla libreria HTTP concreta. */
struct HttpResponse {
    int status_code = 0;
    std::string body;

    bool successful() const noexcept {
        return status_code >= 200 && status_code < 300;
    }
};

/** Trasporto sostituibile usato dai test e dall'implementazione libcurl. */
class IHttpTransport {
public:
    virtual ~IHttpTransport() = default;
    virtual HttpResponse get(const std::string& path) = 0;
    virtual HttpResponse post(
        const std::string& path,
        const std::string& json_body) = 0;
};

/** Trasporto HTTP/HTTPS basato su libcurl. */
class CurlHttpTransport final : public IHttpTransport {
public:
    CurlHttpTransport(
        std::string base_url,
        std::chrono::milliseconds connect_timeout,
        std::chrono::milliseconds request_timeout,
        std::string bearer_token = {});

    HttpResponse get(const std::string& path) override;
    HttpResponse post(
        const std::string& path,
        const std::string& json_body) override;

private:
    HttpResponse request(
        const std::string& method,
        const std::string& path,
        const std::string& json_body);

    std::string base_url_;
    std::chrono::milliseconds connect_timeout_;
    std::chrono::milliseconds request_timeout_;
    std::string bearer_token_;
};

/** Configurazione operativa del client backend. */
struct HttpBackendConfig {
    std::string base_url = "http://127.0.0.1:8000";
    std::string edge_id = "smarthydro-edge";
    std::string boot_id;
    std::string bearer_token;
    std::filesystem::path outbox_directory = "edge-data/outbox";
    std::chrono::milliseconds connect_timeout{1000};
    std::chrono::milliseconds request_timeout{2000};
    std::chrono::milliseconds command_poll_interval{1000};
    std::chrono::milliseconds retry_base_delay{1000};
    std::chrono::milliseconds retry_max_delay{30000};
};

/** Comando ricevuto dalla rete e ancora da eseguire nel thread del runtime. */
struct RemoteRuntimeCommand {
    std::string zone_id;
    RuntimeCommandEnvelope envelope;
};

/**
 * Observer non bloccante che inoltra eventi al backend in un worker dedicato.
 *
 * on_event() serializza e accoda soltanto. Il worker persiste ogni messaggio
 * nell'outbox prima dell'invio, applica retry esponenziale e scarica i comandi.
 */
class HttpBackendClient final : public IEventObserver {
public:
    HttpBackendClient(
        EventBus& event_bus,
        std::vector<std::string> zone_ids,
        HttpBackendConfig config,
        std::shared_ptr<IHttpTransport> transport = nullptr);
    ~HttpBackendClient() override;

    HttpBackendClient(const HttpBackendClient&) = delete;
    HttpBackendClient& operator=(const HttpBackendClient&) = delete;

    void start();
    void stop() noexcept;
    void on_event(const EdgeDomainEvent& event) override;

    /** Estrae i comandi da eseguire fra due cicli del GreenhouseManager. */
    std::vector<RemoteRuntimeCommand> take_commands();

    /**
     * @brief Estrae gli ID delle zone scoperte dal manifesto dell'Edge.
     *
     * Ogni identificatore viene restituito una sola volta, anche dopo retry.
     */
    std::vector<std::string> take_discovered_zone_ids();

    /**
     * @brief Estrae gli ID rimossi da un manifesto backend valido.
     *
     * Errori HTTP, JSON malformato o manifesti non validi non producono
     * rimozioni e lasciano intatta la cache persistente.
     */
    std::vector<std::string> take_removed_zone_ids();

    /** Accoda l'esito di un comando per la consegna idempotente. */
    void submit_command_result(
        const std::string& zone_id,
        const RuntimeCommandResult& result);

    /** Attende al massimo timeout che tutti gli upload vengano confermati. */
    bool flush(std::chrono::milliseconds timeout);
    std::size_t pending_upload_count() const;
    const std::string& boot_id() const noexcept;

private:
    class Impl;
    std::unique_ptr<Impl> impl_;
};

/** Deserializza il contratto JSON restituito dall'endpoint dei comandi. */
RuntimeCommandEnvelope runtime_command_from_json(const std::string& json_text);

}  // namespace smarthydro
