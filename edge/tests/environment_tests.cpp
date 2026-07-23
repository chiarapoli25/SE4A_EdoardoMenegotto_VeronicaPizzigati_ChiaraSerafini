#include "smarthydro/environment_simulator.hpp"

#include <cmath>
#include <stdexcept>

#include <gtest/gtest.h>

namespace {

constexpr double kQuarterHour = 900.0;

TEST(EnvironmentSimulatorTest, UsesAeratedUniversalSoilByDefault) {
    const auto config = smarthydro::make_default_tomato_environment_config();

    EXPECT_EQ(config.soil_type, smarthydro::SoilType::AERATED_UNIVERSAL);
    EXPECT_DOUBLE_EQ(config.initial_ph, 6.3);
    EXPECT_DOUBLE_EQ(config.initial_ec_ms_cm, 1.8);
    EXPECT_STREQ(smarthydro::to_string(smarthydro::SoilType::AERATED_UNIVERSAL),
                 "aerated-universal");
    EXPECT_STREQ(smarthydro::to_string(smarthydro::SoilType::DRAINING),
                 "draining");
    EXPECT_STREQ(smarthydro::to_string(smarthydro::SoilType::ORGANIC_RETENTIVE),
                 "organic-retentive");
}

TEST(EnvironmentSimulatorTest, NaturalLightIsZeroAtNightAndPeaksDuringDay) {
    smarthydro::EnvironmentSimulator environment({}, 10);
    const smarthydro::ActuatorState actuators;

    EXPECT_DOUBLE_EQ(environment.state().light_ppfd_umol_m2_s, 0.0);
    for (int step = 0; step < 48; ++step) {
        environment.step(kQuarterHour, actuators);
    }
    EXPECT_GT(environment.state().light_ppfd_umol_m2_s, 450.0);

    for (int step = 0; step < 48; ++step) {
        environment.step(kQuarterHour, actuators);
    }
    EXPECT_DOUBLE_EQ(environment.state().light_ppfd_umol_m2_s, 0.0);
}

TEST(EnvironmentSimulatorTest, DayIsWarmerAndHumidityRespondsToTemperature) {
    smarthydro::EnvironmentSimulator environment({}, 11);
    const smarthydro::ActuatorState actuators;
    double day_temperature = 0.0;
    double night_temperature = 0.0;
    double day_humidity = 0.0;
    double night_humidity = 0.0;
    int day_samples = 0;
    int night_samples = 0;

    for (int step = 0; step < 192; ++step) {
        environment.step(kQuarterHour, actuators);
        if (step < 96) {
            continue;
        }
        const double hour = std::fmod(environment.state().simulation_time_seconds / 3600.0, 24.0);
        if (hour >= 9.0 && hour <= 18.0) {
            day_temperature += environment.state().temperature_c;
            day_humidity += environment.state().air_humidity_percent;
            ++day_samples;
        } else if (hour <= 5.0 || hour >= 22.0) {
            night_temperature += environment.state().temperature_c;
            night_humidity += environment.state().air_humidity_percent;
            ++night_samples;
        }
    }

    EXPECT_GT(day_temperature / day_samples, night_temperature / night_samples);
    EXPECT_LT(day_humidity / day_samples, night_humidity / night_samples);
}

TEST(EnvironmentSimulatorTest, RemainsBoundedForAWeekWithoutActuators) {
    smarthydro::EnvironmentSimulator environment({}, 12);
    const smarthydro::ActuatorState actuators;

    for (int step = 0; step < 7 * 96; ++step) {
        environment.step(kQuarterHour, actuators);
        const auto& state = environment.state();
        EXPECT_GE(state.temperature_c, 10.0);
        EXPECT_LE(state.temperature_c, 38.0);
        EXPECT_GE(state.air_humidity_percent, 20.0);
        EXPECT_LE(state.air_humidity_percent, 99.0);
        EXPECT_GE(state.ph, 3.0);
        EXPECT_LE(state.ph, 9.0);
        EXPECT_GE(state.ec_ms_cm, 0.0);
        EXPECT_LE(state.ec_ms_cm, 8.0);
        EXPECT_GE(state.root_water_availability, 0.0);
        EXPECT_LE(state.root_water_availability, 1.0);
    }
}

TEST(EnvironmentSimulatorTest, LampsIncreasePpfdAndTemperatureWithSameSeed) {
    smarthydro::EnvironmentSimulator dark({}, 13);
    smarthydro::EnvironmentSimulator lit({}, 13);
    smarthydro::ActuatorState off;
    smarthydro::ActuatorState on;
    on.lighting_percent = 100.0;

    for (int step = 0; step < 16; ++step) {
        dark.step(kQuarterHour, off);
        lit.step(kQuarterHour, on);
    }

    EXPECT_GT(lit.state().light_ppfd_umol_m2_s, dark.state().light_ppfd_umol_m2_s + 390.0);
    EXPECT_GT(lit.state().temperature_c, dark.state().temperature_c + 1.0);
}

TEST(EnvironmentSimulatorTest, SoilTypesDryAtDifferentRates) {
    auto universal_config = smarthydro::make_default_tomato_environment_config();
    auto draining_config = universal_config;
    auto organic_config = universal_config;
    draining_config.soil_type = smarthydro::SoilType::DRAINING;
    organic_config.soil_type = smarthydro::SoilType::ORGANIC_RETENTIVE;
    smarthydro::EnvironmentSimulator universal(universal_config, 14);
    smarthydro::EnvironmentSimulator draining(draining_config, 14);
    smarthydro::EnvironmentSimulator organic(organic_config, 14);
    const double universal_initial = universal.state().root_water_availability;
    const double draining_initial = draining.state().root_water_availability;
    const double organic_initial = organic.state().root_water_availability;
    const smarthydro::ActuatorState off;

    for (int step = 0; step < 96; ++step) {
        universal.step(kQuarterHour, off);
        draining.step(kQuarterHour, off);
        organic.step(kQuarterHour, off);
    }

    const double universal_loss =
        universal_initial - universal.state().root_water_availability;
    const double draining_loss =
        draining_initial - draining.state().root_water_availability;
    const double organic_loss =
        organic_initial - organic.state().root_water_availability;
    EXPECT_GT(draining_loss, universal_loss);
    EXPECT_GT(universal_loss, organic_loss);
}

TEST(EnvironmentSimulatorTest, IrrigationMaintainsWaterWithSoilSpecificResponse) {
    auto universal_config = smarthydro::make_default_tomato_environment_config();
    auto draining_config = universal_config;
    auto organic_config = universal_config;
    draining_config.soil_type = smarthydro::SoilType::DRAINING;
    organic_config.soil_type = smarthydro::SoilType::ORGANIC_RETENTIVE;
    smarthydro::EnvironmentSimulator pumped_universal(universal_config, 19);
    smarthydro::EnvironmentSimulator dry_universal(universal_config, 19);
    smarthydro::EnvironmentSimulator pumped_draining(draining_config, 19);
    smarthydro::EnvironmentSimulator pumped_organic(organic_config, 19);
    smarthydro::ActuatorState pump;
    pump.water_pump_percent = 60.0;
    const smarthydro::ActuatorState off;

    for (int step = 0; step < 4; ++step) {
        pumped_universal.step(kQuarterHour, pump);
        dry_universal.step(kQuarterHour, off);
        pumped_draining.step(kQuarterHour, pump);
        pumped_organic.step(kQuarterHour, pump);
    }

    EXPECT_GT(pumped_universal.state().root_water_availability,
              dry_universal.state().root_water_availability);
    EXPECT_NE(pumped_universal.state().root_water_availability,
              pumped_draining.state().root_water_availability);
    EXPECT_NE(pumped_universal.state().root_water_availability,
              pumped_organic.state().root_water_availability);
}

TEST(EnvironmentSimulatorTest, ManagedIrrigationOutperformsNaturalScenarioForAWeek) {
    smarthydro::EnvironmentSimulator natural({}, 20);
    smarthydro::EnvironmentSimulator managed({}, 20);
    const smarthydro::ActuatorState off;
    smarthydro::ActuatorState irrigation;

    for (int step = 0; step < 7 * 96; ++step) {
        const double water = managed.state().root_water_availability;
        if (water < 0.45) {
            irrigation.water_pump_percent = 60.0;
        } else if (water > 0.72) {
            irrigation.water_pump_percent = 0.0;
        }
        natural.step(kQuarterHour, off);
        managed.step(kQuarterHour, irrigation);
    }

    EXPECT_GT(managed.state().root_water_availability,
              natural.state().root_water_availability + 0.30);
    EXPECT_GE(managed.state().root_water_availability, 0.40);
    EXPECT_LE(managed.state().root_water_availability, 0.85);
}

TEST(EnvironmentSimulatorTest, FertilizerProfileChangesPhAndEc) {
    smarthydro::EnvironmentSimulator baseline({}, 15);
    smarthydro::EnvironmentSimulator fertilized({}, 15);
    smarthydro::ActuatorState off;
    smarthydro::ActuatorState dosing;
    dosing.water_pump_percent = 100.0;
    dosing.selected_fertilizer_id = "tomato-growth";
    dosing.fertilizer_dosing_percent = 100.0;

    baseline.step(3600.0, off);
    fertilized.step(3600.0, dosing);

    EXPECT_LT(fertilized.state().ph, baseline.state().ph);
    EXPECT_GT(fertilized.state().ec_ms_cm, baseline.state().ec_ms_cm + 0.50);
}

TEST(EnvironmentSimulatorTest, RejectsUnknownFertilizerWhenDosing) {
    smarthydro::EnvironmentSimulator environment({}, 16);
    smarthydro::ActuatorState dosing;
    dosing.selected_fertilizer_id = "unknown";
    dosing.fertilizer_dosing_percent = 5.0;
    EXPECT_THROW(environment.step(kQuarterHour, dosing), std::invalid_argument);
}

TEST(EnvironmentSimulatorTest, SeedMakesPhysicalDynamicsReproducible) {
    smarthydro::EnvironmentSimulator first({}, 17);
    smarthydro::EnvironmentSimulator second({}, 17);
    const smarthydro::ActuatorState actuators;

    for (int step = 0; step < 100; ++step) {
        first.step(kQuarterHour, actuators);
        second.step(kQuarterHour, actuators);
    }

    EXPECT_DOUBLE_EQ(first.state().temperature_c, second.state().temperature_c);
    EXPECT_DOUBLE_EQ(first.state().air_humidity_percent, second.state().air_humidity_percent);
    EXPECT_DOUBLE_EQ(first.state().ph, second.state().ph);
    EXPECT_DOUBLE_EQ(first.state().ec_ms_cm, second.state().ec_ms_cm);
    EXPECT_DOUBLE_EQ(first.state().root_water_availability,
                     second.state().root_water_availability);
    EXPECT_DOUBLE_EQ(first.state().light_ppfd_umol_m2_s,
                     second.state().light_ppfd_umol_m2_s);
}

TEST(EnvironmentSimulatorTest, RejectsInvalidTimeStep) {
    smarthydro::EnvironmentSimulator environment({}, 18);
    const smarthydro::ActuatorState actuators;
    EXPECT_THROW(environment.step(0.0, actuators), std::invalid_argument);
    EXPECT_THROW(environment.step(-1.0, actuators), std::invalid_argument);
}

}  // namespace
