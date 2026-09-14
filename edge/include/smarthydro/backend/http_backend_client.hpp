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
    /** @brief Codice di stato HTTP, oppure zero in assenza di risposta. */
    int status_code = 0;
    /** @brief Corpo della risposta senza interpretazione del formato. */
    std::string body;

    /** @brief Verifica se il codice appartiene alla famiglia HTTP 2xx. */
    bool successful() const noexcept {
        return status_code >= 200 && status_code < 300;
    }
};

/** Trasporto sostituibile usato dai test e dall'implementazione libcurl. */
class IHttpTransport {
public:
    virtual ~IHttpTransport() = default;
    /** @brief Esegue una richiesta GET relativa alla base URL configurata. */
    virtual HttpResponse get(const std::string& path) = 0;
    /** @brief Esegue una richiesta POST JSON relativa alla base URL configurata. */
    virtual HttpResponse post(
        const std::string& path,
        const std::string& json_body) = 0;
};

/** Trasporto HTTP/HTTPS basato su libcurl. */
class CurlHttpTransport final : public IHttpTransport {
public:
    /** @brief Configura URL, timeout e Bearer token del trasporto libcurl. */
    CurlHttpTransport(
        std::string base_url,
        std::chrono::milliseconds connect_timeout,
        std::chrono::milliseconds request_timeout,
        std::string bearer_token = {});

    /** @copydoc IHttpTransport::get() */
    HttpResponse get(const std::string& path) override;
    /** @copydoc IHttpTransport::post() */
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
    /** @brief Base URL del backend senza slash finale. */
    std::string base_url = "http://127.0.0.1:8000";
    /** @brief Identificatore stabile dell'Edge presso il backend. */
    std::string edge_id = "smarthydro-edge";
    /** @brief Identificatore del singolo avvio del processo. */
    std::string boot_id;
    /** @brief Token tecnico allegato alle richieste `/api/v1`. */
    std::string bearer_token;
    /** @brief Directory dell'outbox persistente e del manifesto zone. */
    std::filesystem::path outbox_directory = "edge-data/outbox";
    /** @brief Timeout di apertura della connessione. */
    std::chrono::milliseconds connect_timeout{1000};
    /** @brief Timeout complessivo di una richiesta. */
    std::chrono::milliseconds request_timeout{2000};
    /** @brief Intervallo di polling della coda comandi. */
    std::chrono::milliseconds command_poll_interval{1000};
    /** @brief Ritardo iniziale del backoff esponenziale. */
    std::chrono::milliseconds retry_base_delay{1000};
    /** @brief Tetto del backoff esponenziale. */
    std::chrono::milliseconds retry_max_delay{30000};
};

/** Comando ricevuto dalla rete e ancora da eseguire nel thread del runtime. */
struct RemoteRuntimeCommand {
    /** @brief Zona alla quale indirizzare il comando. */
    std::string zone_id;
    /** @brief Envelope idempotente deserializzato dal backend. */
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
    /** @brief Crea il worker HTTP e lo collega all'EventBus condiviso. */
    HttpBackendClient(
        EventBus& event_bus,
        std::vector<std::string> zone_ids,
        HttpBackendConfig config,
        std::shared_ptr<IHttpTransport> transport = nullptr);
    ~HttpBackendClient() override;

    HttpBackendClient(const HttpBackendClient&) = delete;
    HttpBackendClient& operator=(const HttpBackendClient&) = delete;

    /** @brief Avvia il worker asincrono; idempotente. */
    void start();
    /** @brief Arresta e ricongiunge il worker; idempotente. */
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
    /** @brief Restituisce il numero di elementi ancora presenti nell'outbox. */
    std::size_t pending_upload_count() const;
    /** @brief Restituisce l'identificatore dell'avvio corrente. */
    const std::string& boot_id() const noexcept;

private:
    class Impl;
    std::unique_ptr<Impl> impl_;
};

/** Deserializza il contratto JSON restituito dall'endpoint dei comandi. */
RuntimeCommandEnvelope runtime_command_from_json(const std::string& json_text);

}  // namespace smarthydro
