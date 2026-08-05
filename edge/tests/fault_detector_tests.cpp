#include <smarthydro/faults/fault_detector.hpp>
#include <smarthydro/faults/fault_injector.hpp>

#include <gtest/gtest.h>

#include <limits>
#include <optional>
#include <string>
#include <vector>

namespace {

smarthydro::SensorReadings valid_readings(double timestamp = 0.0) {
    smarthydro::SensorReadings readings;
    readings.timestamp_seconds = timestamp;
    readings.temperature_c = 24.0;
    readings.air_humidity_percent = 60.0;
    readings.soil_moisture_percent = 55.0;
    readings.soil_bulk_ec_ms_cm = 1.2;
    readings.soil_ec_ms_cm = 1.8;
    readings.fertilizer_concentration_mg_per_liter = 400.0;
    readings.nitrogen_estimate_mg_per_liter = 120.0;
    readings.phosphorus_estimate_mg_per_liter = 60.0;
    readings.potassium_estimate_mg_per_liter = 180.0;
    readings.ph = 6.2;
    readings.light_ppfd_umol_m2_s = 500.0;
    return readings;
}

smarthydro::ControlledValues<smarthydro::ValueRange> safety_ranges() {
    return {{
        {0.0, 100.0},
        {0.0, 3000.0},
        {0.0, 14.0},
        {0.0, 350.0},
        {0.0, 350.0},
        {0.0, 500.0},
    }};
}

const smarthydro::DetectedFault* find_rule(
    const std::vector<smarthydro::DetectedFault>& faults,
    const std::string& rule) {
    for (const auto& fault : faults) {
        if (fault.rule == rule) return &fault;
    }
    return nullptr;
}

void disable_frozen_detection(smarthydro::FaultDetectorConfig& config) {
    for (auto& policy : config.values) policy.detect_frozen = false;
}

TEST(FaultDetectorTest, MissingValueUsesConfiguredCycleThreshold) {
    smarthydro::FaultDetectorConfig config;
    config.missing_cycles = 2;
    disable_frozen_detection(config);
    smarthydro::FaultDetector detector(config);
    auto readings = valid_readings();
    readings.soil_moisture_percent.reset();

    const auto first = detector.observe_readings(readings, safety_ranges());
    readings.timestamp_seconds = 60.0;
    const auto second = detector.observe_readings(readings, safety_ranges());

    EXPECT_EQ(find_rule(first, "missing_value"), nullptr);
    const auto* fault = find_rule(second, "missing_value");
    ASSERT_NE(fault, nullptr);
    EXPECT_EQ(fault->component, "soil_moisture_sensor");
    EXPECT_EQ(fault->severity, smarthydro::ControlFaultSeverity::RECOVERABLE);
}

TEST(FaultDetectorTest, DetectsPhysicalRangeAndMaximumRate) {
    smarthydro::FaultDetectorConfig config;
    disable_frozen_detection(config);
    config.values[smarthydro::observed_value_index(
        smarthydro::ObservedValue::SOIL_MOISTURE)].maximum_rate_per_second = 1.0;
    smarthydro::FaultDetector detector(config);
    auto readings = valid_readings();
    detector.observe_readings(readings, safety_ranges());

    readings.timestamp_seconds = 1.0;
    readings.soil_moisture_percent = 70.0;
    const auto rate_faults = detector.observe_readings(
        readings, safety_ranges());
    readings.timestamp_seconds = 2.0;
    readings.soil_moisture_percent = 101.0;
    const auto range_faults = detector.observe_readings(
        readings, safety_ranges());

    ASSERT_NE(find_rule(rate_faults, "maximum_rate_exceeded"), nullptr);
    const auto* range = find_rule(range_faults, "outside_physical_range");
    ASSERT_NE(range, nullptr);
    EXPECT_EQ(range->severity, smarthydro::ControlFaultSeverity::CRITICAL);
}

TEST(FaultDetectorTest, DetectsFrozenValueAfterConfiguredCycles) {
    smarthydro::FaultDetectorConfig config;
    config.frozen_cycles = 3;
    disable_frozen_detection(config);
    config.values[smarthydro::observed_value_index(
        smarthydro::ObservedValue::PH)].detect_frozen = true;
    smarthydro::FaultDetector detector(config);
    auto readings = valid_readings();

    EXPECT_EQ(find_rule(
        detector.observe_readings(readings, safety_ranges()),
        "frozen_value"), nullptr);
    readings.timestamp_seconds = 60.0;
    EXPECT_EQ(find_rule(
        detector.observe_readings(readings, safety_ranges()),
        "frozen_value"), nullptr);
    readings.timestamp_seconds = 120.0;
    EXPECT_NE(find_rule(
        detector.observe_readings(readings, safety_ranges()),
        "frozen_value"), nullptr);
}

TEST(FaultDetectorTest, ValidatesNutrientsAgainstModelLimitsAndFiniteness) {
    smarthydro::FaultDetectorConfig config;
    disable_frozen_detection(config);
    smarthydro::FaultDetector detector(config);
    auto readings = valid_readings();
    readings.nitrogen_estimate_mg_per_liter =
        std::numeric_limits<double>::quiet_NaN();
    const auto non_finite = detector.observe_readings(
        readings, safety_ranges());

    detector.reset();
    readings = valid_readings(60.0);
    readings.potassium_estimate_mg_per_liter = 700.0;
    const auto incompatible = detector.observe_readings(
        readings, safety_ranges());

    const auto* invalid = find_rule(non_finite, "non_finite_model_value");
    ASSERT_NE(invalid, nullptr);
    EXPECT_EQ(invalid->component, "nitrogen_model");
    const auto* limits = find_rule(incompatible, "model_limit_incompatible");
    ASSERT_NE(limits, nullptr);
    EXPECT_EQ(limits->component, "potassium_model");
}

TEST(FaultDetectorTest, DetectsCommandWithoutResponseAfterThreshold) {
    smarthydro::FaultDetectorConfig config;
    config.actuator_no_response_cycles = 2;
    smarthydro::FaultDetector detector(config);
    smarthydro::ActuatorCommand command;
    command.lighting_percent = 50.0;
    const smarthydro::ActuatorOutput output;
    const smarthydro::ActuatorConfig actuator_config;

    const auto first = detector.observe_actuators(
        command, output, actuator_config, 60.0);
    const auto second = detector.observe_actuators(
        command, output, actuator_config, 60.0);

    EXPECT_EQ(find_rule(first, "commanded_without_response"), nullptr);
    const auto* fault = find_rule(second, "commanded_without_response");
    ASSERT_NE(fault, nullptr);
    EXPECT_EQ(fault->component, "lighting");
}

TEST(FaultDetectorTest, ActiveWithoutCommandIsImmediatelyCritical) {
    smarthydro::FaultDetector detector;
    const smarthydro::ActuatorCommand command;
    smarthydro::ActuatorOutput output;
    output.lighting_power_watts = 200.0;

    const auto faults = detector.observe_actuators(
        command, output, smarthydro::ActuatorConfig{}, 60.0);

    const auto* fault = find_rule(faults, "active_without_command");
    ASSERT_NE(fault, nullptr);
    EXPECT_EQ(fault->severity, smarthydro::ControlFaultSeverity::CRITICAL);
}

TEST(FaultDetectorTest, LowResponseMustPersistBeforeDetection) {
    smarthydro::FaultDetectorConfig config;
    config.actuator_low_response_cycles = 2;
    config.minimum_actuator_response_ratio = 0.5;
    smarthydro::FaultDetector detector(config);
    smarthydro::ActuatorCommand command;
    command.lighting_percent = 100.0;
    smarthydro::ActuatorOutput output;
    output.lighting_power_watts = 40.0;

    const auto first = detector.observe_actuators(
        command, output, smarthydro::ActuatorConfig{}, 60.0);
    const auto second = detector.observe_actuators(
        command, output, smarthydro::ActuatorConfig{}, 60.0);

    EXPECT_EQ(find_rule(first, "persistent_low_response"), nullptr);
    const auto* fault = find_rule(second, "persistent_low_response");
    ASSERT_NE(fault, nullptr);
    EXPECT_EQ(fault->severity, smarthydro::ControlFaultSeverity::RECOVERABLE);
}

TEST(FaultInjectorTest, AltersReadingsWithoutProducingDetectionMetadata) {
    smarthydro::FaultInjector injector;
    injector.inject(
        {
            "offset",
            smarthydro::FaultTargetKind::SENSOR,
            "ph",
            smarthydro::FaultMode::SENSOR_OFFSET,
            0.5,
            std::nullopt,
        },
        0.0);
    auto readings = valid_readings();

    injector.alter_readings(readings, 0.0);

    ASSERT_TRUE(readings.ph.has_value());
    EXPECT_DOUBLE_EQ(*readings.ph, 6.7);
    EXPECT_TRUE(injector.contains("offset"));
    EXPECT_TRUE(injector.reset("offset"));
}

TEST(FaultDetectorTest, RejectsInvalidConfiguration) {
    smarthydro::FaultDetectorConfig config;
    config.missing_cycles = 0;
    EXPECT_THROW(
        smarthydro::FaultDetector detector(config),
        std::invalid_argument);
}

}  // namespace
