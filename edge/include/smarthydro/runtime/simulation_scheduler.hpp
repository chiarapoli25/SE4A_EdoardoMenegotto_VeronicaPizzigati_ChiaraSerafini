#pragma once

/**
 * @file simulation_scheduler.hpp
 * @brief Scheduler multi-zona che lega tempo reale e passi simulativi fissi.
 */

#include <smarthydro/runtime/greenhouse_manager.hpp>

#include <chrono>
#include <cstddef>
#include <map>
#include <string>
#include <vector>

namespace smarthydro {

/** @brief Parametri stabili del ciclo temporale del servizio Edge. */
struct SimulationSchedulerConfig {
    /** Durata simulata di ogni chiamata a EdgeRuntime::step(). */
    double step_seconds = 900.0;
    /** Numero massimo globale di passi eseguiti in una iterazione. */
    std::size_t max_catch_up_steps = 8;
};

/** Risultati prodotti in una iterazione, raggruppati per zona. */
using ScheduledStepResults =
    std::map<std::string, std::vector<EdgeStepResult>>;

/**
 * @brief Accumula tempo simulato e distribuisce equamente i passi dovuti.
 *
 * Il clock non viene letto internamente: il chiamante passa esplicitamente un
 * time_point di steady_clock, rendendo tutti i rapporti temporali testabili
 * senza attese reali.
 */
class SimulationScheduler {
public:
    using Clock = std::chrono::steady_clock;
    using TimePoint = Clock::time_point;

    /**
     * @brief Collega lo scheduler a un manager e valida la configurazione.
     * @throws std::invalid_argument Per quantum non positivo/non finito o
     *     budget di recupero nullo.
     */
    explicit SimulationScheduler(
        GreenhouseManager& greenhouse,
        SimulationSchedulerConfig config = {});

    /**
     * @brief Accumula il tempo trascorso con lifecycle e time_scale correnti.
     *
     * Una zona Running accumula `elapsed_real * time_scale`; Paused aggiorna
     * soltanto l'istante reale; gli stati inattivi eliminano la pianificazione.
     * @throws std::logic_error Se il time_point precede quello gia acquisito.
     */
    void accrue(TimePoint now);

    /**
     * @brief Allinea lo stato temporale dopo l'esecuzione dei comandi remoti.
     *
     * Una nuova zona Running/Paused viene inizializzata con accumulatore zero;
     * Idle ed Error cancellano anche l'eventuale backlog precedente.
     */
    void synchronize(TimePoint now);

    /**
     * @brief Esegue al massimo il budget globale di passi con round-robin.
     *
     * Il residuo rimane accumulato e produce eventi soltanto quando una zona
     * entra o esce dallo stato di ritardo.
     */
    ScheduledStepResults run_due_steps();

    /** @brief Secondi simulati accumulati, zero per zone non pianificate. */
    double accumulated_simulation_seconds(
        const std::string& zone_id) const noexcept;
    /** @brief Numero di quantum completi ancora pendenti. */
    std::size_t pending_step_count(
        const std::string& zone_id) const noexcept;
    /** @brief Configurazione validata usata dallo scheduler. */
    const SimulationSchedulerConfig& config() const noexcept;

private:
    struct ZoneSchedule {
        TimePoint last_real_time{};
        double accumulated_simulation_seconds = 0.0;
        bool lagging = false;
    };

    bool zone_has_due_step(const std::string& zone_id) const;
    std::size_t pending_steps(const ZoneSchedule& schedule) const noexcept;
    void publish_lag_transition(
        const std::string& zone_id,
        ZoneSchedule& schedule,
        bool lagging) noexcept;

    GreenhouseManager& greenhouse_;
    SimulationSchedulerConfig config_;
    std::map<std::string, ZoneSchedule> schedules_;
    std::size_t round_robin_cursor_ = 0;
};

}  // namespace smarthydro
