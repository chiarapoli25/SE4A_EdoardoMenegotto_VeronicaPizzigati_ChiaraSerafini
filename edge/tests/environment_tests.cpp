#include "smarthydro/environment_simulator.hpp"

#include <algorithm>
#include <cmath>
#include <stdexcept>

#include <gtest/gtest.h>

namespace {

constexpr double kQuarterHour = 900.0;

TEST(EnvironmentSimulatorTest, UsesAeratedUniversalSoilByDefault) {
    const smarthydro::EnvironmentConfig config;

    EXPECT_EQ(config.soil_type, smarthydro::SoilType::AERATED_UNIVERSAL);
    EXPECT_DOUBLE_EQ(config.initial_ph, 6.3);
    EXPECT_DOUBLE_EQ(config.initial_ec_ms_cm, 1.8);
    EXPECT_DOUBLE_EQ(config.mean_cloud_transmission, 0.82);
    EXPECT_GT(config.daily_cloud_transmission_stddev, 0.0);
    EXPECT_GT(config.hourly_cloud_transmission_stddev, 0.0);
    EXPECT_STREQ(smarthydro::to_string(smarthydro::SoilType::AERATED_UNIVERSAL),
                 "aerated-universal");
    EXPECT_STREQ(smarthydro::to_string(smarthydro::SoilType::DRAINING),
                 "draining");
    EXPECT_STREQ(smarthydro::to_string(smarthydro::SoilType::ORGANIC_RETENTIVE),
                 "organic-retentive");
}

TEST(EnvironmentSimulatorTest, NaturalLightIsZeroAtNightAndPeaksDuringDay) {
    smarthydro::EnvironmentSimulator environment({}, 10);
    const smarthydro::ActuatorOutput actuators;

    EXPECT_DOUBLE_EQ(environment.state().light_ppfd_umol_m2_s, 0.0);
    for (int step = 0; step < 48; ++step) {
        environment.step(kQuarterHour, actuators);
    }
    // Anche una giornata molto nuvolosa deve conservare luce naturale
    // significativa, senza presumere che il seed produca cielo sereno.
    EXPECT_GT(environment.state().light_ppfd_umol_m2_s, 100.0);

    for (int step = 0; step < 48; ++step) {
        environment.step(kQuarterHour, actuators);
    }
    EXPECT_DOUBLE_EQ(environment.state().light_ppfd_umol_m2_s, 0.0);
}

TEST(EnvironmentSimulatorTest, DailyCloudRegimesChangeAvailableNaturalLight) {
    smarthydro::EnvironmentConfig config;
    config.hourly_cloud_transmission_stddev = 0.0;
    smarthydro::EnvironmentSimulator environment(config, 110);
    const smarthydro::ActuatorOutput actuators;
    double previous_noon_ppfd = -1.0;
    bool observed_different_days = false;

    for (int step = 0; step < 5 * 96; ++step) {
        environment.step(kQuarterHour, actuators);
        const double hour =
            std::fmod(environment.state().simulation_time_seconds / 3600.0, 24.0);
        if (std::abs(hour - 12.0) < 1e-9) {
            const double noon_ppfd = environment.state().light_ppfd_umol_m2_s;
            if (previous_noon_ppfd >= 0.0 &&
                std::abs(noon_ppfd - previous_noon_ppfd) > 1.0) {
                observed_different_days = true;
            }
            previous_noon_ppfd = noon_ppfd;
        }
    }

    EXPECT_TRUE(observed_different_days);
}

TEST(EnvironmentSimulatorTest, HourlyCloudsVaryAroundAFixedDailyRegime) {
    smarthydro::EnvironmentConfig config;
    config.daily_cloud_transmission_stddev = 0.0;
    config.hourly_cloud_transmission_stddev = 0.15;
    smarthydro::EnvironmentSimulator environment(config, 111);
    const smarthydro::ActuatorOutput actuators;
    double minimum_transmission = 1.0;
    double maximum_transmission = 0.0;

    for (int step = 0; step < 96; ++step) {
        environment.step(kQuarterHour, actuators);
        const double hour =
            std::fmod(environment.state().simulation_time_seconds / 3600.0, 24.0);
        const double relative_hour = hour - config.sunrise_hour;
        if (relative_hour <= 0.0 || relative_hour >= config.photoperiod_hours) {
            continue;
        }
        const double sun_factor =
            std::sin(3.14159265358979323846 * relative_hour /
                     config.photoperiod_hours);
        const double transmission =
            environment.state().light_ppfd_umol_m2_s /
            (config.natural_light_peak_ppfd * sun_factor);
        minimum_transmission = std::min(minimum_transmission, transmission);
        maximum_transmission = std::max(maximum_transmission, transmission);
    }

    EXPECT_GT(maximum_transmission - minimum_transmission, 0.10);
}

TEST(EnvironmentSimulatorTest, DayIsWarmerAndHumidityRespondsToTemperature) {
    smarthydro::EnvironmentSimulator environment({}, 11);
    const smarthydro::ActuatorOutput actuators;
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
    const smarthydro::ActuatorOutput actuators;

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
        EXPECT_GE(state.soil_moisture_percent, 0.0);
        EXPECT_LE(state.soil_moisture_percent, 100.0);
    }
}

TEST(EnvironmentSimulatorTest, LampsIncreasePpfdAndTemperatureWithSameSeed) {
    smarthydro::EnvironmentSimulator dark({}, 13);
    smarthydro::EnvironmentSimulator lit({}, 13);
    smarthydro::ActuatorOutput off;
    smarthydro::ActuatorOutput on;
    on.lighting_power_watts = 200.0;

    for (int step = 0; step < 16; ++step) {
        dark.step(kQuarterHour, off);
        lit.step(kQuarterHour, on);
    }

    EXPECT_GT(lit.state().light_ppfd_umol_m2_s, dark.state().light_ppfd_umol_m2_s + 390.0);
    EXPECT_GT(lit.state().temperature_c, dark.state().temperature_c + 1.0);
}

TEST(EnvironmentSimulatorTest, SoilTypesDryAtDifferentRates) {
    smarthydro::EnvironmentConfig universal_config;
    auto draining_config = universal_config;
    auto organic_config = universal_config;
    draining_config.soil_type = smarthydro::SoilType::DRAINING;
    organic_config.soil_type = smarthydro::SoilType::ORGANIC_RETENTIVE;
    smarthydro::EnvironmentSimulator universal(universal_config, 14);
    smarthydro::EnvironmentSimulator draining(draining_config, 14);
    smarthydro::EnvironmentSimulator organic(organic_config, 14);
    const double universal_initial = universal.state().soil_moisture_percent;
    const double draining_initial = draining.state().soil_moisture_percent;
    const double organic_initial = organic.state().soil_moisture_percent;
    const smarthydro::ActuatorOutput off;

    for (int step = 0; step < 96; ++step) {
        universal.step(kQuarterHour, off);
        draining.step(kQuarterHour, off);
        organic.step(kQuarterHour, off);
    }

    const double universal_loss =
        universal_initial - universal.state().soil_moisture_percent;
    const double draining_loss =
        draining_initial - draining.state().soil_moisture_percent;
    const double organic_loss =
        organic_initial - organic.state().soil_moisture_percent;
    EXPECT_GT(draining_loss, universal_loss);
    EXPECT_GT(universal_loss, organic_loss);
}

TEST(EnvironmentSimulatorTest, IrrigationMaintainsWaterWithSoilSpecificResponse) {
    smarthydro::EnvironmentConfig universal_config;
    auto draining_config = universal_config;
    auto organic_config = universal_config;
    draining_config.soil_type = smarthydro::SoilType::DRAINING;
    organic_config.soil_type = smarthydro::SoilType::ORGANIC_RETENTIVE;
    smarthydro::EnvironmentSimulator pumped_universal(universal_config, 19);
    smarthydro::EnvironmentSimulator dry_universal(universal_config, 19);
    smarthydro::EnvironmentSimulator pumped_draining(draining_config, 19);
    smarthydro::EnvironmentSimulator pumped_organic(organic_config, 19);
    smarthydro::ActuatorOutput pump;
    pump.irrigation_volume_liters_last_step = 0.3;
    const smarthydro::ActuatorOutput off;

    for (int step = 0; step < 4; ++step) {
        pumped_universal.step(kQuarterHour, pump);
        dry_universal.step(kQuarterHour, off);
        pumped_draining.step(kQuarterHour, pump);
        pumped_organic.step(kQuarterHour, pump);
    }

    EXPECT_GT(pumped_universal.state().soil_moisture_percent,
              dry_universal.state().soil_moisture_percent);
    EXPECT_NE(pumped_universal.state().soil_moisture_percent,
              pumped_draining.state().soil_moisture_percent);
    EXPECT_NE(pumped_universal.state().soil_moisture_percent,
              pumped_organic.state().soil_moisture_percent);
}

TEST(EnvironmentSimulatorTest, DeliveredWaterVolumeChangesSoilMoisture) {
    smarthydro::EnvironmentSimulator dry({}, 21);
    smarthydro::EnvironmentSimulator irrigated({}, 21);
    const smarthydro::ActuatorOutput off;
    smarthydro::ActuatorOutput irrigation;
    irrigation.irrigation_volume_liters_last_step = 0.1;

    // Con capacita utile di 4 L ed efficienza del 90%, 0,1 L aumentano
    // l'umidita di 2,25 punti percentuali.
    dry.step(kQuarterHour, off);
    irrigated.step(kQuarterHour, irrigation);

    EXPECT_NEAR(
        irrigated.state().soil_moisture_percent -
            dry.state().soil_moisture_percent,
        2.25,
        1e-9);
}

TEST(EnvironmentSimulatorTest, ManagedIrrigationOutperformsNaturalScenarioForAWeek) {
    smarthydro::EnvironmentSimulator natural({}, 20);
    smarthydro::EnvironmentSimulator managed({}, 20);
    const smarthydro::ActuatorOutput off;
    smarthydro::ActuatorOutput irrigation;

    for (int step = 0; step < 7 * 96; ++step) {
        const double moisture = managed.state().soil_moisture_percent;
        if (moisture < 45.0) {
            irrigation.irrigation_volume_liters_last_step = 0.3;
        } else if (moisture > 72.0) {
            irrigation.irrigation_volume_liters_last_step = 0.0;
        }
        natural.step(kQuarterHour, off);
        managed.step(kQuarterHour, irrigation);
    }

    EXPECT_GT(managed.state().soil_moisture_percent,
              natural.state().soil_moisture_percent + 30.0);
    EXPECT_GE(managed.state().soil_moisture_percent, 40.0);
    EXPECT_LE(managed.state().soil_moisture_percent, 85.0);
}

TEST(EnvironmentSimulatorTest, NutrientProductsAffectOnlyTheirOwnConcentration) {
    smarthydro::EnvironmentSimulator baseline({}, 15);
    smarthydro::EnvironmentSimulator nitrogen({}, 15);
    smarthydro::EnvironmentSimulator phosphorus({}, 15);
    smarthydro::EnvironmentSimulator potassium({}, 15);
    smarthydro::ActuatorOutput water;
    water.irrigation_volume_liters_last_step = 0.5;
    auto nitrogen_dose = water;
    auto phosphorus_dose = water;
    auto potassium_dose = water;
    nitrogen_dose.fertilizer_volume_milliliters_last_step[
        smarthydro::fertilizer_index(smarthydro::FertilizerType::NITROGEN)] = 1.0;
    phosphorus_dose.fertilizer_volume_milliliters_last_step[
        smarthydro::fertilizer_index(smarthydro::FertilizerType::PHOSPHORUS)] = 1.0;
    potassium_dose.fertilizer_volume_milliliters_last_step[
        smarthydro::fertilizer_index(smarthydro::FertilizerType::POTASSIUM)] = 1.0;

    baseline.step(300.0, water);
    nitrogen.step(300.0, nitrogen_dose);
    phosphorus.step(300.0, phosphorus_dose);
    potassium.step(300.0, potassium_dose);

    EXPECT_GT(nitrogen.state().nitrogen_mg_per_liter,
              baseline.state().nitrogen_mg_per_liter);
    EXPECT_DOUBLE_EQ(nitrogen.state().phosphorus_mg_per_liter,
                     baseline.state().phosphorus_mg_per_liter);
    EXPECT_DOUBLE_EQ(nitrogen.state().potassium_mg_per_liter,
                     baseline.state().potassium_mg_per_liter);

    EXPECT_DOUBLE_EQ(phosphorus.state().nitrogen_mg_per_liter,
                     baseline.state().nitrogen_mg_per_liter);
    EXPECT_GT(phosphorus.state().phosphorus_mg_per_liter,
              baseline.state().phosphorus_mg_per_liter);
    EXPECT_DOUBLE_EQ(phosphorus.state().potassium_mg_per_liter,
                     baseline.state().potassium_mg_per_liter);

    EXPECT_DOUBLE_EQ(potassium.state().nitrogen_mg_per_liter,
                     baseline.state().nitrogen_mg_per_liter);
    EXPECT_DOUBLE_EQ(potassium.state().phosphorus_mg_per_liter,
                     baseline.state().phosphorus_mg_per_liter);
    EXPECT_GT(potassium.state().potassium_mg_per_liter,
              baseline.state().potassium_mg_per_liter);
}

TEST(EnvironmentSimulatorTest, PhCorrectorsChangePhWithoutAddingNutrients) {
    smarthydro::EnvironmentSimulator baseline({}, 16);
    smarthydro::EnvironmentSimulator ph_up({}, 16);
    smarthydro::EnvironmentSimulator ph_down({}, 16);
    smarthydro::ActuatorOutput water;
    water.irrigation_volume_liters_last_step = 0.5;
    auto up_dose = water;
    auto down_dose = water;
    up_dose.fertilizer_volume_milliliters_last_step[
        smarthydro::fertilizer_index(smarthydro::FertilizerType::PH_UP)] = 1.0;
    down_dose.fertilizer_volume_milliliters_last_step[
        smarthydro::fertilizer_index(smarthydro::FertilizerType::PH_DOWN)] = 1.0;

    baseline.step(300.0, water);
    ph_up.step(300.0, up_dose);
    ph_down.step(300.0, down_dose);

    EXPECT_GT(ph_up.state().ph, baseline.state().ph);
    EXPECT_LT(ph_down.state().ph, baseline.state().ph);
    EXPECT_DOUBLE_EQ(ph_up.state().nitrogen_mg_per_liter,
                     baseline.state().nitrogen_mg_per_liter);
    EXPECT_DOUBLE_EQ(ph_up.state().phosphorus_mg_per_liter,
                     baseline.state().phosphorus_mg_per_liter);
    EXPECT_DOUBLE_EQ(ph_up.state().potassium_mg_per_liter,
                     baseline.state().potassium_mg_per_liter);
}

TEST(EnvironmentSimulatorTest, FertilizerRequiresWaterAndOppositePhIsRejected) {
    smarthydro::EnvironmentSimulator environment({}, 17);
    smarthydro::ActuatorOutput without_water;
    without_water.fertilizer_volume_milliliters_last_step[
        smarthydro::fertilizer_index(smarthydro::FertilizerType::NITROGEN)] = 1.0;
    EXPECT_THROW(
        environment.step(300.0, without_water),
        std::invalid_argument);

    smarthydro::ActuatorOutput conflicting_ph;
    conflicting_ph.irrigation_volume_liters_last_step = 0.5;
    conflicting_ph.fertilizer_volume_milliliters_last_step[
        smarthydro::fertilizer_index(smarthydro::FertilizerType::PH_UP)] = 1.0;
    conflicting_ph.fertilizer_volume_milliliters_last_step[
        smarthydro::fertilizer_index(smarthydro::FertilizerType::PH_DOWN)] = 1.0;
    EXPECT_THROW(
        environment.step(300.0, conflicting_ph),
        std::invalid_argument);
}

TEST(EnvironmentSimulatorTest, IrrigationDilutesNutrientsAndUptakeStaysNonNegative) {
    smarthydro::EnvironmentConfig no_uptake;
    no_uptake.nitrogen_uptake_milligrams_per_hour = 0.0;
    no_uptake.phosphorus_uptake_milligrams_per_hour = 0.0;
    no_uptake.potassium_uptake_milligrams_per_hour = 0.0;
    smarthydro::EnvironmentSimulator dry(no_uptake, 18);
    smarthydro::EnvironmentSimulator irrigated(no_uptake, 18);
    smarthydro::ActuatorOutput water;
    water.irrigation_volume_liters_last_step = 0.5;

    dry.step(300.0, {});
    irrigated.step(300.0, water);

    EXPECT_LT(irrigated.state().nitrogen_mg_per_liter,
              dry.state().nitrogen_mg_per_liter);
    EXPECT_LT(irrigated.state().phosphorus_mg_per_liter,
              dry.state().phosphorus_mg_per_liter);
    EXPECT_LT(irrigated.state().potassium_mg_per_liter,
              dry.state().potassium_mg_per_liter);

    smarthydro::EnvironmentConfig strong_uptake;
    strong_uptake.initial_nitrogen_mg_per_liter = 0.01;
    strong_uptake.initial_phosphorus_mg_per_liter = 0.01;
    strong_uptake.initial_potassium_mg_per_liter = 0.01;
    strong_uptake.nitrogen_uptake_milligrams_per_hour = 1000.0;
    strong_uptake.phosphorus_uptake_milligrams_per_hour = 1000.0;
    strong_uptake.potassium_uptake_milligrams_per_hour = 1000.0;
    smarthydro::EnvironmentSimulator depleted(strong_uptake, 19);
    depleted.step(3600.0, {});
    EXPECT_GE(depleted.state().nitrogen_mg_per_liter, 0.0);
    EXPECT_GE(depleted.state().phosphorus_mg_per_liter, 0.0);
    EXPECT_GE(depleted.state().potassium_mg_per_liter, 0.0);
}

TEST(EnvironmentSimulatorTest, DrainageRemovesMixedNutrientMass) {
    smarthydro::EnvironmentConfig config;
    config.soil_type = smarthydro::SoilType::DRAINING;
    config.nitrogen_uptake_milligrams_per_hour = 0.0;
    config.phosphorus_uptake_milligrams_per_hour = 0.0;
    config.potassium_uptake_milligrams_per_hour = 0.0;
    smarthydro::EnvironmentSimulator environment(config, 20);
    smarthydro::ActuatorOutput mixture;
    mixture.irrigation_volume_liters_last_step = 2.0;
    mixture.fertilizer_volume_milliliters_last_step[
        smarthydro::fertilizer_index(smarthydro::FertilizerType::NITROGEN)] = 1.0;

    // Il terriccio drenante parte al 65% di 3 L: 292.5 mg di N.
    // Il prodotto ne aggiunge 50 mg; dopo il drenaggio deve rimanerne meno
    // dei 342.5 mg disponibili prima della perdita di soluzione.
    environment.step(300.0, mixture);
    const double final_root_water_liters =
        environment.state().soil_moisture_percent / 100.0 * 3.0;
    const double inferred_nitrogen_mass =
        environment.state().nitrogen_mg_per_liter * final_root_water_liters;

    EXPECT_GT(inferred_nitrogen_mass, 0.0);
    EXPECT_LT(inferred_nitrogen_mass, 342.5);
}

TEST(EnvironmentSimulatorTest, ActuatorMixtureUpdatesWaterNutrientsEcAndPh) {
    smarthydro::ActuatorSimulator actuators;
    smarthydro::EnvironmentSimulator baseline({}, 20);
    smarthydro::EnvironmentSimulator treated({}, 20);
    actuators.request_irrigation_volume_liters(0.5);
    actuators.set_fertilizer_valve_open(
        smarthydro::FertilizerType::NITROGEN, true);
    actuators.set_fertilizer_valve_open(
        smarthydro::FertilizerType::PHOSPHORUS, true);
    actuators.set_fertilizer_valve_open(
        smarthydro::FertilizerType::POTASSIUM, true);
    actuators.set_fertilizer_valve_open(
        smarthydro::FertilizerType::PH_DOWN, true);
    actuators.step(900.0);

    smarthydro::ActuatorOutput water_only;
    water_only.irrigation_volume_liters_last_step =
        actuators.output().irrigation_volume_liters_last_step;
    baseline.step(900.0, water_only);
    treated.step(900.0, actuators.output());

    EXPECT_GT(treated.state().nitrogen_mg_per_liter,
              baseline.state().nitrogen_mg_per_liter);
    EXPECT_GT(treated.state().phosphorus_mg_per_liter,
              baseline.state().phosphorus_mg_per_liter);
    EXPECT_GT(treated.state().potassium_mg_per_liter,
              baseline.state().potassium_mg_per_liter);
    EXPECT_GT(treated.state().ec_ms_cm, baseline.state().ec_ms_cm);
    EXPECT_LT(treated.state().ph, baseline.state().ph);
}

TEST(EnvironmentSimulatorTest, SplitsWaterAndAllFertilizersAcrossSubsteps) {
    smarthydro::EnvironmentSimulator single_step({}, 21);
    smarthydro::EnvironmentSimulator three_steps({}, 21);
    smarthydro::ActuatorOutput combined;
    combined.irrigation_volume_liters_last_step = 0.3;
    combined.fertilizer_volume_milliliters_last_step[
        smarthydro::fertilizer_index(smarthydro::FertilizerType::NITROGEN)] = 3.0;
    combined.fertilizer_volume_milliliters_last_step[
        smarthydro::fertilizer_index(smarthydro::FertilizerType::PH_DOWN)] = 1.5;
    smarthydro::ActuatorOutput partial = combined;
    partial.irrigation_volume_liters_last_step /= 3.0;
    for (double& volume :
         partial.fertilizer_volume_milliliters_last_step) {
        volume /= 3.0;
    }

    single_step.step(900.0, combined);
    for (int step = 0; step < 3; ++step) {
        three_steps.step(300.0, partial);
    }

    EXPECT_NEAR(single_step.state().soil_moisture_percent,
                three_steps.state().soil_moisture_percent, 1e-12);
    EXPECT_NEAR(single_step.state().nitrogen_mg_per_liter,
                three_steps.state().nitrogen_mg_per_liter, 1e-12);
    EXPECT_NEAR(single_step.state().phosphorus_mg_per_liter,
                three_steps.state().phosphorus_mg_per_liter, 1e-12);
    EXPECT_NEAR(single_step.state().potassium_mg_per_liter,
                three_steps.state().potassium_mg_per_liter, 1e-12);
    EXPECT_NEAR(single_step.state().ph, three_steps.state().ph, 1e-12);
    EXPECT_NEAR(single_step.state().ec_ms_cm, three_steps.state().ec_ms_cm, 1e-12);
}

TEST(EnvironmentSimulatorTest, SeedMakesPhysicalDynamicsReproducible) {
    smarthydro::EnvironmentSimulator first({}, 17);
    smarthydro::EnvironmentSimulator second({}, 17);
    const smarthydro::ActuatorOutput actuators;

    for (int step = 0; step < 100; ++step) {
        first.step(kQuarterHour, actuators);
        second.step(kQuarterHour, actuators);
    }

    EXPECT_DOUBLE_EQ(first.state().temperature_c, second.state().temperature_c);
    EXPECT_DOUBLE_EQ(first.state().air_humidity_percent, second.state().air_humidity_percent);
    EXPECT_DOUBLE_EQ(first.state().ph, second.state().ph);
    EXPECT_DOUBLE_EQ(first.state().ec_ms_cm, second.state().ec_ms_cm);
    EXPECT_DOUBLE_EQ(first.state().nitrogen_mg_per_liter,
                     second.state().nitrogen_mg_per_liter);
    EXPECT_DOUBLE_EQ(first.state().phosphorus_mg_per_liter,
                     second.state().phosphorus_mg_per_liter);
    EXPECT_DOUBLE_EQ(first.state().potassium_mg_per_liter,
                     second.state().potassium_mg_per_liter);
    EXPECT_DOUBLE_EQ(first.state().soil_moisture_percent,
                     second.state().soil_moisture_percent);
    EXPECT_DOUBLE_EQ(first.state().light_ppfd_umol_m2_s,
                     second.state().light_ppfd_umol_m2_s);
}

TEST(EnvironmentSimulatorTest, RejectsInvalidTimeStep) {
    smarthydro::EnvironmentSimulator environment({}, 18);
    const smarthydro::ActuatorOutput actuators;
    EXPECT_THROW(environment.step(0.0, actuators), std::invalid_argument);
    EXPECT_THROW(environment.step(-1.0, actuators), std::invalid_argument);
}

TEST(EnvironmentSimulatorTest, RejectsInvalidCloudConfiguration) {
    smarthydro::EnvironmentConfig config;
    config.mean_cloud_transmission = 1.1;
    EXPECT_THROW(
        smarthydro::EnvironmentSimulator(config, 22),
        std::invalid_argument);

    config = {};
    config.daily_cloud_transmission_stddev = -0.1;
    EXPECT_THROW(
        smarthydro::EnvironmentSimulator(config, 22),
        std::invalid_argument);

    config = {};
    config.cloud_persistence_hours = 0.0;
    EXPECT_THROW(
        smarthydro::EnvironmentSimulator(config, 22),
        std::invalid_argument);
}

TEST(EnvironmentSimulatorTest, RejectsInvalidNutrientProfilesAndLimits) {
    smarthydro::EnvironmentConfig config;
    config.fertilizer_profiles[
        smarthydro::fertilizer_index(smarthydro::FertilizerType::NITROGEN)]
        .type = smarthydro::FertilizerType::POTASSIUM;
    EXPECT_THROW(
        smarthydro::EnvironmentSimulator(config, 23),
        std::invalid_argument);

    config = {};
    config.fertilizer_profiles[
        smarthydro::fertilizer_index(smarthydro::FertilizerType::PH_UP)]
        .ph_change_per_milliliter = -0.01;
    EXPECT_THROW(
        smarthydro::EnvironmentSimulator(config, 23),
        std::invalid_argument);

    config = {};
    config.maximum_nutrient_concentration_mg_per_liter = 100.0;
    EXPECT_THROW(
        smarthydro::EnvironmentSimulator(config, 23),
        std::invalid_argument);
}

}  // namespace
