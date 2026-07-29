#include "smarthydro/edge_runtime.hpp"
#include "smarthydro/recipe_json.hpp"

#include <gtest/gtest.h>

#include <cmath>
#include <limits>
#include <string>
#include <utility>
#include <variant>

namespace {

smarthydro::Recipe load_demo_recipe() {
    return smarthydro::load_recipe_json(
        SMARTHYDRO_EXAMPLE_RECIPE_PATH);
}

smarthydro::SensorConfig deterministic_sensors() {
    smarthydro::SensorConfig config;
    for (auto* channel : {
             &config.temperature,
             &config.air_humidity,
             &config.soil_moisture,
             &config.ph,
             &config.light_ppfd}) {
        channel->bias = 0.0;
        channel->noise_standard_deviation = 0.0;
        channel->resolution = 0.0;
        channel->dropout_probability = 0.0;
        channel->calibration_correction = 0.0;
    }
    return config;
}

TEST(EdgeRuntimeTest, BlocksRecipeUntilConfigurationsAreConfirmed) {
    smarthydro::EdgeRuntime runtime(
        load_demo_recipe(),
        {},
        {},
        deterministic_sensors());

    const auto result = runtime.step(60.0);

    for (const auto& decision : result.decisions) {
        EXPECT_EQ(
            decision.status,
            smarthydro::ControlDecisionStatus::BLOCKED);
        EXPECT_NE(
            decision.message.find("not confirmed"),
            std::string::npos);
    }
    EXPECT_DOUBLE_EQ(result.delivered_water_liters, 0.0);
    EXPECT_DOUBLE_EQ(
        result.environment_state.simulation_time_seconds,
        60.0);
}

TEST(EdgeRuntimeTest, ExecutesConfirmedRecipeOnPhysicalSimulators) {
    auto recipe = load_demo_recipe();
    auto& soil_target =
        recipe.phases.front().targets[
            smarthydro::controlled_variable_index(
                smarthydro::ControlledVariable::SOIL_MOISTURE)];
    soil_target.setpoint = 85.0;
    soil_target.allowed_range = {80.0, 90.0};
    soil_target.safety_range = {0.0, 100.0};

    smarthydro::EnvironmentConfig environment_config;
    environment_config.initial_nitrogen_mg_per_liter = 100.0;
    smarthydro::EdgeRuntime runtime(
        std::move(recipe),
        {},
        environment_config,
        deterministic_sensors());
    runtime.confirm_all_configurations();

    const auto result = runtime.step(900.0);
    const auto water_index =
        smarthydro::controlled_variable_index(
            smarthydro::ControlledVariable::SOIL_MOISTURE);
    const auto nitrogen_index =
        smarthydro::controlled_variable_index(
            smarthydro::ControlledVariable::NITROGEN);
    const double delivered_nitrogen =
        result.delivered_fertilizer_milliliters[
            smarthydro::fertilizer_index(
                smarthydro::FertilizerType::NITROGEN)];

    EXPECT_EQ(
        result.decisions[water_index].status,
        smarthydro::ControlDecisionStatus::APPLIED);
    EXPECT_DOUBLE_EQ(
        result.decisions[water_index].command,
        0.5);
    EXPECT_NE(
        result.decisions[nitrogen_index].status,
        smarthydro::ControlDecisionStatus::BLOCKED);
    EXPECT_GT(
        result.decisions[nitrogen_index].command,
        0.0);
    EXPECT_NEAR(result.delivered_water_liters, 0.5, 1.0e-12);
    EXPECT_NEAR(
        delivered_nitrogen,
        result.decisions[nitrogen_index].command,
        1.0e-12);
    EXPECT_NEAR(
        runtime.cumulative_phase_dose_milliliters(
            smarthydro::ControlledVariable::NITROGEN),
        delivered_nitrogen,
        1.0e-12);
    EXPECT_NEAR(
        runtime.daily_dose_milliliters(
            smarthydro::ControlledVariable::NITROGEN),
        delivered_nitrogen,
        1.0e-12);
    EXPECT_GT(
        result.environment_state.nitrogen_mg_per_liter,
        environment_config.initial_nitrogen_mg_per_liter);
    EXPECT_DOUBLE_EQ(
        result.environment_state.simulation_time_seconds,
        900.0);
}

TEST(EdgeRuntimeTest, RejectsInvalidStepDuration) {
    smarthydro::EdgeRuntime runtime(
        load_demo_recipe(),
        {},
        {},
        deterministic_sensors());

    EXPECT_THROW(runtime.step(0.0), std::invalid_argument);
    EXPECT_THROW(
        runtime.step(
            std::numeric_limits<double>::quiet_NaN()),
        std::invalid_argument);
}

TEST(EdgeRuntimeTest, ProducesMonotonicSequencesAndStartupEvent) {
    smarthydro::EdgeRuntime runtime(
        load_demo_recipe(),
        {},
        {},
        deterministic_sensors());
    runtime.confirm_all_configurations();

    const auto first = runtime.step(60.0);
    const auto second = runtime.step(60.0);

    EXPECT_EQ(first.sequence_number, 0U);
    EXPECT_EQ(second.sequence_number, 1U);
    EXPECT_EQ(
        first.operational_state,
        smarthydro::OperationalState::NOMINAL);
    EXPECT_EQ(
        runtime.operational_state(),
        smarthydro::OperationalState::NOMINAL);
    ASSERT_EQ(first.events.size(), 1U);
    EXPECT_EQ(
        first.events.front().type,
        smarthydro::EdgeEventType::RUNTIME_STARTED);
    EXPECT_TRUE(second.events.empty());
}

TEST(EdgeRuntimeTest, ReportsRecipePhaseTransition) {
    auto recipe = load_demo_recipe();
    recipe.phases.front().duration_hours = 0.01;
    smarthydro::EdgeRuntime runtime(
        std::move(recipe),
        {},
        {},
        deterministic_sensors());
    runtime.confirm_all_configurations();

    const auto first = runtime.step(60.0);
    const auto second = runtime.step(60.0);

    EXPECT_EQ(first.phase_name, "VegetativeGrowth");
    EXPECT_EQ(second.phase_name, "Flowering");
    ASSERT_EQ(second.events.size(), 1U);
    EXPECT_EQ(
        second.events.front().type,
        smarthydro::EdgeEventType::RECIPE_PHASE_CHANGED);
    EXPECT_NE(
        second.events.front().message.find("Flowering"),
        std::string::npos);
}

}  // namespace
