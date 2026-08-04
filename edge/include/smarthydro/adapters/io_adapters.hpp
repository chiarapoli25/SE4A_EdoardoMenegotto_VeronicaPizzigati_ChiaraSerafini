#pragma once

/**
 * @file io_adapters.hpp
 * @brief Interfacce Edge e Adapter per simulatori, sensori e attuatori.
 */

#include <smarthydro/simulation/actuator_simulator.hpp>
#include <smarthydro/simulation/environment_simulator.hpp>
#include <smarthydro/simulation/sensor_simulator.hpp>

#include <array>
#include <cstddef>
#include <cstdint>
#include <memory>
#include <optional>

namespace smarthydro {

/** @brief Canali fisici acquisiti dal runtime tramite ISensor. */
enum class SensorChannel : std::size_t {
    TEMPERATURE = 0,
    AIR_HUMIDITY,
    SOIL_MOISTURE,
    SOIL_CONDUCTIVITY,
    PH,
    LIGHT,
    COUNT,
};

/** Numero dei canali fisici letti dal runtime. */
constexpr std::size_t kSensorChannelCount =
    static_cast<std::size_t>(SensorChannel::COUNT);

/** Collezione indicizzata tramite SensorChannel. */
using SensorAdapterArray =
    std::array<std::unique_ptr<class ISensor>, kSensorChannelCount>;

/** @brief Converte un canale sensore nel relativo indice. */
std::size_t sensor_channel_index(SensorChannel channel);

/**
 * @brief Contratto di un singolo canale sensore usato dall'Edge.
 *
 * Un adapter hardware puo ignorare environment_state e interrogare il proprio
 * dispositivo. Gli adapter simulati lo usano invece come sorgente fisica.
 */
class ISensor {
public:
    virtual ~ISensor() = default;

    /** @brief Identifica senza ambiguita il canale fornito dall'istanza. */
    virtual SensorChannel channel() const noexcept = 0;

    /**
     * @brief Legge il canale senza modificare lo stato ambientale.
     * @return Valore nell'unita del canale o std::nullopt in caso di guasto.
     */
    virtual std::optional<double> read(
        const EnvironmentState& environment_state) = 0;
};

/**
 * @brief Contratto del driver aggregato degli attuatori della zona.
 *
 * Il runtime usa esclusivamente questa interfaccia. Un'implementazione puo
 * delegare al simulatore, a GPIO, Modbus, MQTT o a un altro protocollo.
 */
class IActuator {
public:
    virtual ~IActuator() = default;

    /** @brief Restituisce i comandi logici correnti. */
    virtual const ActuatorCommand& command() const noexcept = 0;
    /** @brief Restituisce le uscite fisiche correnti. */
    virtual const ActuatorOutput& output() const noexcept = 0;
    /** @brief Restituisce la configurazione fisica validata. */
    virtual const ActuatorConfig& config() const noexcept = 0;
    /** @brief Richiede l'erogazione del volume d'acqua indicato. */
    virtual void request_irrigation_volume_liters(double volume_liters) = 0;
    /** @brief Annulla l'irrigazione e chiude le valvole. */
    virtual void cancel_irrigation() noexcept = 0;
    /** @brief Restituisce il tempo residuo dell'irrigazione, in secondi. */
    virtual double remaining_irrigation_time_seconds() const noexcept = 0;
    /** @brief Imposta lo stato della valvola del prodotto indicato. */
    virtual void set_fertilizer_valve_open(
        FertilizerType type,
        bool open) = 0;
    /** @brief Chiude tutte le valvole dei concentrati. */
    virtual void close_all_fertilizer_valves() noexcept = 0;
    /** @brief Restituisce lo stato fisico della valvola indicata. */
    virtual bool fertilizer_valve_open(FertilizerType type) const = 0;
    /** @brief Imposta il comando delle lampade nell'intervallo [0, 100]. */
    virtual void set_lighting_command_percent(double value) = 0;
    /** @brief Fa avanzare le uscite fisiche degli attuatori. */
    virtual void step(double delta_time_seconds) = 0;
    /** @brief Porta immediatamente tutti gli attuatori nello stato sicuro. */
    virtual void stop_all() noexcept = 0;
};

/** @brief Contratto dell'ambiente fisico osservato e aggiornato dall'Edge. */
class IEnvironment {
public:
    virtual ~IEnvironment() = default;

    /** @brief Restituisce lo stato fisico corrente. */
    virtual const EnvironmentState& state() const noexcept = 0;

    /** @brief Applica all'ambiente le uscite fisiche dell'ultimo passo. */
    virtual void step(
        double delta_time_seconds,
        const ActuatorOutput& actuator_output) = 0;
};

/**
 * @brief Campionatore condiviso dagli adapter dei sensori simulati.
 *
 * La cache garantisce che i sei adapter osservino lo stesso SensorReadings
 * quando vengono interrogati allo stesso timestamp.
 */
class SimulatedSensorSampler {
public:
    /** @brief Costruisce il simulatore condiviso con configurazione e seed. */
    SimulatedSensorSampler(SensorConfig config, std::uint32_t seed);

    /** @brief Restituisce il campione sincronizzato per lo stato indicato. */
    const SensorReadings& sample(const EnvironmentState& environment_state);

private:
    SensorSimulator simulator_;
    std::optional<double> cached_timestamp_seconds_;
    SensorReadings cached_readings_;
};

/** @brief Adapter del canale temperatura prodotto da SensorSimulator. */
class TemperatureSensorAdapter final : public ISensor {
public:
    /** @brief Collega il canale al campionatore simulato condiviso. */
    explicit TemperatureSensorAdapter(
        std::shared_ptr<SimulatedSensorSampler> sampler);
    SensorChannel channel() const noexcept override;
    std::optional<double> read(
        const EnvironmentState& environment_state) override;

private:
    std::shared_ptr<SimulatedSensorSampler> sampler_;
};

/** @brief Adapter del canale umidita dell'aria prodotto da SensorSimulator. */
class AirHumiditySensorAdapter final : public ISensor {
public:
    /** @brief Collega il canale al campionatore simulato condiviso. */
    explicit AirHumiditySensorAdapter(
        std::shared_ptr<SimulatedSensorSampler> sampler);
    SensorChannel channel() const noexcept override;
    std::optional<double> read(
        const EnvironmentState& environment_state) override;

private:
    std::shared_ptr<SimulatedSensorSampler> sampler_;
};

/** @brief Adapter del sensore simulato di umidita del substrato. */
class SoilMoistureSensorAdapter final : public ISensor {
public:
    /** @brief Collega il canale al campionatore simulato condiviso. */
    explicit SoilMoistureSensorAdapter(
        std::shared_ptr<SimulatedSensorSampler> sampler);
    SensorChannel channel() const noexcept override;
    std::optional<double> read(
        const EnvironmentState& environment_state) override;

private:
    std::shared_ptr<SimulatedSensorSampler> sampler_;
};

/** @brief Adapter della sonda resistiva di conducibilita del terriccio. */
class SoilConductivitySensorAdapter final : public ISensor {
public:
    /** @brief Collega il canale al campionatore simulato condiviso. */
    explicit SoilConductivitySensorAdapter(
        std::shared_ptr<SimulatedSensorSampler> sampler);
    SensorChannel channel() const noexcept override;
    std::optional<double> read(
        const EnvironmentState& environment_state) override;

private:
    std::shared_ptr<SimulatedSensorSampler> sampler_;
};

/** @brief Adapter dell'elettrodo di pH simulato. */
class PhSensorAdapter final : public ISensor {
public:
    /** @brief Collega il canale al campionatore simulato condiviso. */
    explicit PhSensorAdapter(
        std::shared_ptr<SimulatedSensorSampler> sampler);
    SensorChannel channel() const noexcept override;
    std::optional<double> read(
        const EnvironmentState& environment_state) override;

private:
    std::shared_ptr<SimulatedSensorSampler> sampler_;
};

/** @brief Adapter del sensore PAR simulato. */
class LightSensorAdapter final : public ISensor {
public:
    /** @brief Collega il canale al campionatore simulato condiviso. */
    explicit LightSensorAdapter(
        std::shared_ptr<SimulatedSensorSampler> sampler);
    SensorChannel channel() const noexcept override;
    std::optional<double> read(
        const EnvironmentState& environment_state) override;

private:
    std::shared_ptr<SimulatedSensorSampler> sampler_;
};

/**
 * @brief Crea i sei adapter simulati con un campionatore condiviso.
 * @return Array completo, indicizzato tramite SensorChannel.
 */
SensorAdapterArray make_simulated_sensor_adapters(
    SensorConfig config = {},
    std::uint32_t seed = 0x53484D32U);

/** @brief Adapter che espone ActuatorSimulator tramite IActuator. */
class ActuatorSimulatorAdapter final : public IActuator {
public:
    /** @brief Costruisce il simulatore incapsulato nello stato sicuro. */
    explicit ActuatorSimulatorAdapter(ActuatorConfig config = {});

    /** @copydoc IActuator::command() */
    const ActuatorCommand& command() const noexcept override;
    /** @copydoc IActuator::output() */
    const ActuatorOutput& output() const noexcept override;
    /** @copydoc IActuator::config() */
    const ActuatorConfig& config() const noexcept override;
    /** @copydoc IActuator::request_irrigation_volume_liters() */
    void request_irrigation_volume_liters(double volume_liters) override;
    /** @copydoc IActuator::cancel_irrigation() */
    void cancel_irrigation() noexcept override;
    /** @copydoc IActuator::remaining_irrigation_time_seconds() */
    double remaining_irrigation_time_seconds() const noexcept override;
    /** @copydoc IActuator::set_fertilizer_valve_open() */
    void set_fertilizer_valve_open(
        FertilizerType type,
        bool open) override;
    /** @copydoc IActuator::close_all_fertilizer_valves() */
    void close_all_fertilizer_valves() noexcept override;
    /** @copydoc IActuator::fertilizer_valve_open() */
    bool fertilizer_valve_open(FertilizerType type) const override;
    /** @copydoc IActuator::set_lighting_command_percent() */
    void set_lighting_command_percent(double value) override;
    /** @copydoc IActuator::step() */
    void step(double delta_time_seconds) override;
    /** @copydoc IActuator::stop_all() */
    void stop_all() noexcept override;

private:
    ActuatorSimulator simulator_;
};

/** @brief Adapter di dominio per i comandi della pompa dell'acqua. */
class WaterPumpAdapter {
public:
    /** @brief Collega la pompa al driver aggregato indicato. */
    explicit WaterPumpAdapter(IActuator& actuator);

    /** @brief Traduce una dose d'acqua nella richiesta al driver. */
    void request_volume_liters(double volume_liters);
    /** @brief Annulla in sicurezza l'irrigazione corrente. */
    void cancel() noexcept;
    /** @brief Indica se la pompa e fisicamente attiva. */
    bool active() const noexcept;
    /** @brief Restituisce i secondi ancora necessari per la dose. */
    double remaining_time_seconds() const noexcept;

private:
    IActuator& actuator_;
};

/** @brief Adapter di dominio per il comando percentuale delle lampade. */
class LightingAdapter {
public:
    /** @brief Collega le lampade al driver aggregato indicato. */
    explicit LightingAdapter(IActuator& actuator);

    /** @brief Traduce la percentuale nel comando del driver. */
    void set_command_percent(double value);
    /** @brief Restituisce la potenza elettrica fisicamente applicata. */
    double power_watts() const noexcept;

private:
    IActuator& actuator_;
};

/** @brief Adapter di dominio per le cinque elettrovalvole dei concentrati. */
class FertilizerValveAdapter {
public:
    /** @brief Collega le valvole al driver aggregato indicato. */
    explicit FertilizerValveAdapter(IActuator& actuator);

    /** @brief Apre o chiude la valvola del prodotto indicato. */
    void set_open(FertilizerType type, bool open);
    /** @brief Chiude simultaneamente tutte le valvole. */
    void close_all() noexcept;
    /** @brief Restituisce lo stato fisico della valvola indicata. */
    bool open(FertilizerType type) const;

private:
    IActuator& actuator_;
};

/** @brief Adapter che espone EnvironmentSimulator tramite IEnvironment. */
class EnvironmentSimulatorAdapter final : public IEnvironment {
public:
    /** @brief Costruisce l'ambiente simulato con un seed casuale. */
    explicit EnvironmentSimulatorAdapter(
        EnvironmentConfig config = {});
    /** @brief Costruisce l'ambiente simulato con un seed riproducibile. */
    EnvironmentSimulatorAdapter(
        EnvironmentConfig config,
        std::uint32_t seed);

    /** @copydoc IEnvironment::state() */
    const EnvironmentState& state() const noexcept override;
    /** @copydoc IEnvironment::step() */
    void step(
        double delta_time_seconds,
        const ActuatorOutput& actuator_output) override;

private:
    EnvironmentSimulator simulator_;
};

}  // namespace smarthydro
