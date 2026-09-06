#include <smarthydro/control/control_system.hpp>
#include <smarthydro/recipes/recipe_json.hpp>

#include <memory>
#include <stdexcept>
#include <string>
#include <utility>

#include <gtest/gtest.h>

namespace {

smarthydro::Recipe load_demo_recipe() {
    return smarthydro::load_recipe_json(SMARTHYDRO_EXAMPLE_RECIPE_PATH);
}

void confirm_all(smarthydro::RecipeControlSystem& system) {
    for (std::size_t index = 0;
         index < smarthydro::kControlledVariableCount;
         ++index) {
        const auto variable =
            static_cast<smarthydro::ControlledVariable>(index);
        const auto result = system.confirm_configuration(variable);
        ASSERT_TRUE(result.success) << result.error;
    }
    ASSERT_TRUE(system.all_configurations_confirmed());
}

TEST(ControllerStrategyTest, FactoryCreatesCommonInterface) {
    smarthydro::ControllerParameters parameters =
        smarthydro::ThresholdConfig{
            40.0,
            60.0,
            smarthydro::ControlDirection::INCREASES_PROCESS_VALUE,
            0.5,
            0.0,
            false};
    std::unique_ptr<smarthydro::IController> controller =
        smarthydro::ControllerFactory::create(
            smarthydro::StrategyType::THRESHOLD, parameters);
    smarthydro::ControllerInput input;
    input.measured_value = 35.0;

    const auto result = controller->compute(input);

    EXPECT_EQ(controller->strategy_type(), smarthydro::StrategyType::THRESHOLD);
    ASSERT_TRUE(result.valid);
    EXPECT_DOUBLE_EQ(result.command, 0.5);
}

TEST(RecipeJsonTest, LoadsAndRoundTripsDemonstrationRecipe) {
    const auto recipe = load_demo_recipe();

    ASSERT_EQ(recipe.phases.size(), 2U);
    EXPECT_EQ(recipe.plant_type, "Tomato");
    ASSERT_TRUE(recipe.substrate.has_value());
    EXPECT_EQ(
        *recipe.substrate,
        smarthydro::SoilType::AERATED_UNIVERSAL);
    EXPECT_EQ(recipe.phases.front().name, "VegetativeGrowth");
    EXPECT_EQ(
        recipe.controllers[smarthydro::controlled_variable_index(
            smarthydro::ControlledVariable::NITROGEN)]
            .default_strategy,
        smarthydro::StrategyType::PREDICTIVE);

    const auto round_trip =
        smarthydro::recipe_from_json(smarthydro::recipe_to_json(recipe));
    EXPECT_EQ(round_trip.id, recipe.id);
    EXPECT_EQ(round_trip.version, recipe.version);
    EXPECT_EQ(round_trip.phases.size(), recipe.phases.size());
    EXPECT_DOUBLE_EQ(
        round_trip.phases[1]
            .targets[smarthydro::controlled_variable_index(
                smarthydro::ControlledVariable::POTASSIUM)]
            .setpoint,
        240.0);
}

TEST(RecipeJsonTest, SerializesCanonicalInputSourceAndReadsLegacySensor) {
    const auto recipe = load_demo_recipe();
    const auto canonical = smarthydro::recipe_to_json(recipe);

    EXPECT_NE(canonical.find("\"input_source\""), std::string::npos);
    EXPECT_EQ(canonical.find("\"sensor\":"), std::string::npos);

    auto legacy = canonical;
    const std::string canonical_key = "\"input_source\"";
    const std::string legacy_key = "\"sensor\"";
    std::size_t position = 0;
    while ((position = legacy.find(canonical_key, position)) !=
           std::string::npos) {
        legacy.replace(position, canonical_key.size(), legacy_key);
        position += legacy_key.size();
    }

    const auto loaded = smarthydro::recipe_from_json(legacy);
    EXPECT_EQ(loaded.id, recipe.id);
    EXPECT_EQ(
        loaded.controllers[smarthydro::controlled_variable_index(
            smarthydro::ControlledVariable::NITROGEN)]
            .input_source,
        smarthydro::ControlInputSource::NITROGEN_MODEL);
}

TEST(RecipeJsonTest, RejectsRecipeWithoutExplicitSubstrate) {
    auto recipe = load_demo_recipe();
    auto json = smarthydro::recipe_to_json(recipe);
    const std::string substrate_key = "  \"substrate\":";
    const auto substrate_start = json.find(substrate_key);
    ASSERT_NE(substrate_start, std::string::npos);
    const auto substrate_end = json.find('\n', substrate_start);
    ASSERT_NE(substrate_end, std::string::npos);
    json.erase(
        substrate_start,
        substrate_end - substrate_start + 1);

    EXPECT_THROW(
        smarthydro::recipe_from_json(json),
        std::invalid_argument);

    recipe.substrate.reset();

    EXPECT_THROW(
        smarthydro::RecipeControlSystem::validate_recipe(recipe),
        std::invalid_argument);
    EXPECT_THROW(
        smarthydro::recipe_to_json(recipe),
        std::invalid_argument);
}

TEST(RecipeControlSystemTest, RecipeDefinesAllRequiredDefaultStrategies) {
    const auto recipe = load_demo_recipe();
    for (std::size_t index = 0; index < 2; ++index) {
        EXPECT_EQ(
            recipe.controllers[index].default_strategy,
            smarthydro::StrategyType::THRESHOLD);
    }
    const auto ph_index = smarthydro::controlled_variable_index(
        smarthydro::ControlledVariable::PH);
    EXPECT_EQ(
        recipe.controllers[ph_index].default_strategy,
        smarthydro::StrategyType::PID);
    EXPECT_EQ(
        recipe.controllers[ph_index].selected_strategy,
        smarthydro::StrategyType::PID);
    for (std::size_t index = 3;
         index < smarthydro::kControlledVariableCount;
         ++index) {
        EXPECT_EQ(
            recipe.controllers[index].default_strategy,
            smarthydro::StrategyType::PREDICTIVE);
    }
    const auto& ph_parameters =
        std::get<smarthydro::PidConfig>(
            recipe.controllers[ph_index].parameters);
    EXPECT_LT(ph_parameters.command_limits.minimum, 0.0);
    EXPECT_GT(ph_parameters.command_limits.maximum, 0.0);
}

TEST(RecipeControlSystemTest, DefaultPhPidProducesSignedSmallDoses) {
    smarthydro::RecipeControlSystem system(load_demo_recipe());
    confirm_all(system);
    smarthydro::ControlRequest request;
    request.controller_input.delta_time_seconds = 60.0;
    request.seconds_since_last_dose = 4000.0;

    request.controller_input.measured_value = 5.8;
    const auto ph_up =
        system.execute(smarthydro::ControlledVariable::PH, request);
    EXPECT_NE(ph_up.status, smarthydro::ControlDecisionStatus::BLOCKED);
    EXPECT_GT(ph_up.command, 0.0);
    EXPECT_LE(ph_up.command, 0.5);

    request.controller_input.measured_value = 6.6;
    const auto ph_down =
        system.execute(smarthydro::ControlledVariable::PH, request);
    EXPECT_NE(ph_down.status, smarthydro::ControlDecisionStatus::BLOCKED);
    EXPECT_LT(ph_down.command, 0.0);
    EXPECT_GE(ph_down.command, -0.5);
}

TEST(RecipeControlSystemTest, ConfirmsAutomaticallyOnRecipeAdoption) {
    // Adopting a recipe (construction here, replace_recipe() below) already
    // confirms every controller from its selected_strategy — no separate
    // ConfirmConfiguration command is required before the first execute().
    smarthydro::RecipeControlSystem system(load_demo_recipe());
    EXPECT_TRUE(system.all_configurations_confirmed());
    EXPECT_EQ(
        system.recipe().controllers[0].confirmation_state,
        smarthydro::ConfirmationState::CONFIRMED);

    smarthydro::ControlRequest request;
    request.controller_input.measured_value = 40.0;
    const auto active = system.execute(
        smarthydro::ControlledVariable::SOIL_MOISTURE, request);
    EXPECT_NE(active.status, smarthydro::ControlDecisionStatus::BLOCKED);
    EXPECT_DOUBLE_EQ(active.command, 0.5);
}

TEST(RecipeControlSystemTest, PhaseTransitionKeepsConfirmedVersion) {
    smarthydro::RecipeControlSystem system(load_demo_recipe());
    confirm_all(system);
    const auto version = system.recipe().version;

    EXPECT_EQ(system.active_phase(0.0).name, "VegetativeGrowth");
    EXPECT_EQ(system.active_phase(400.0).name, "Flowering");
    EXPECT_EQ(system.recipe().version, version);
    EXPECT_TRUE(system.all_configurations_confirmed());

    smarthydro::ControlRequest request;
    request.elapsed_recipe_hours = 400.0;
    request.controller_input.measured_value = 40.0;
    EXPECT_NE(
        system.execute(smarthydro::ControlledVariable::SOIL_MOISTURE, request)
            .status,
        smarthydro::ControlDecisionStatus::BLOCKED);
}

TEST(RecipeControlSystemTest, StrategyChangeInvalidatesPreviousConfirmation) {
    smarthydro::RecipeControlSystem system(load_demo_recipe());
    confirm_all(system);
    const auto old_version = system.recipe().version;

    system.select_strategy(
        smarthydro::ControlledVariable::SOIL_MOISTURE,
        smarthydro::StrategyType::PID,
        smarthydro::PidConfig{
            60.0,
            0.1,
            0.001,
            0.0,
            {0.0, 1.0},
            smarthydro::ControlDirection::INCREASES_PROCESS_VALUE});

    EXPECT_GT(system.recipe().version, old_version);
    EXPECT_FALSE(system.all_configurations_confirmed());
    EXPECT_EQ(
        system.recipe().controllers[0].confirmation_state,
        smarthydro::ConfirmationState::PENDING_CONFIRMATION);
    EXPECT_TRUE(
        system.confirm_configuration(
                  smarthydro::ControlledVariable::SOIL_MOISTURE)
            .success);

    smarthydro::ControlRequest request;
    request.controller_input.measured_value = 40.0;
    request.controller_input.delta_time_seconds = 60.0;
    const auto decision = system.execute(
        smarthydro::ControlledVariable::SOIL_MOISTURE, request);
    EXPECT_NE(decision.status, smarthydro::ControlDecisionStatus::BLOCKED);
    EXPECT_GT(decision.command, 0.0);
}

TEST(RecipeControlSystemTest, AcceptsThresholdStrategyForNutrientModelSource) {
    // N/P/K leggono da una sorgente di modello (is_model_source), non da un
    // sensore diretto, ma process_value()/source_value() risolvono il
    // valore in modo identico per qualunque Strategy — Threshold e PID sono
    // quindi ammesse quanto Predictive (che resta il default storico, non
    // l'unica scelta valida: vedi required_default_strategy() in
    // control_system.cpp).
    smarthydro::RecipeControlSystem system(load_demo_recipe());
    system.select_strategy(
        smarthydro::ControlledVariable::NITROGEN,
        smarthydro::StrategyType::THRESHOLD,
        smarthydro::ThresholdConfig{
            130.0,
            170.0,
            smarthydro::ControlDirection::INCREASES_PROCESS_VALUE,
            1.0,
            0.0,
            false});

    const auto result =
        system.confirm_configuration(smarthydro::ControlledVariable::NITROGEN);
    ASSERT_TRUE(result.success) << result.error;
    EXPECT_EQ(
        system.recipe()
            .controllers[smarthydro::controlled_variable_index(
                smarthydro::ControlledVariable::NITROGEN)]
            .confirmation_state,
        smarthydro::ConfirmationState::CONFIRMED);

    smarthydro::ControlRequest request;
    // Sotto la soglia bassa di fase (130, presa da allowed_range — vedi
    // parameters_for_phase()): il Threshold deve attivarsi esattamente come
    // per qualunque altra variabile.
    request.controller_input.model_estimate = 100.0;
    const auto decision =
        system.execute(smarthydro::ControlledVariable::NITROGEN, request);

    EXPECT_NE(decision.status, smarthydro::ControlDecisionStatus::BLOCKED);
    EXPECT_DOUBLE_EQ(decision.command, 1.0);
}

TEST(RecipeControlSystemTest, AppliesWaterVolumeAndPumpDurationLimits) {
    auto recipe = load_demo_recipe();
    auto& water = recipe.controllers[0];
    std::get<smarthydro::ThresholdConfig>(water.parameters).active_command = 2.0;
    water.output_limits.maximum_water_volume_liters = 5.0;
    water.output_limits.maximum_pump_duration_seconds = 900.0;
    water.output_limits.water_pump_flow_liters_per_hour = 2.0;
    smarthydro::RecipeControlSystem system(std::move(recipe));
    confirm_all(system);
    smarthydro::ControlRequest request;
    request.controller_input.measured_value = 40.0;

    const auto decision = system.execute(
        smarthydro::ControlledVariable::SOIL_MOISTURE, request);

    EXPECT_EQ(decision.status, smarthydro::ControlDecisionStatus::LIMITED);
    EXPECT_DOUBLE_EQ(decision.command, 0.5);
}

TEST(RecipeControlSystemTest, EnforcesPhSettlingDailyDoseAndMutualExclusion) {
    smarthydro::RecipeControlSystem system(load_demo_recipe());
    confirm_all(system);
    smarthydro::ControlRequest request;
    request.controller_input.measured_value = 5.9;
    request.seconds_since_last_dose = 100.0;

    auto decision =
        system.execute(smarthydro::ControlledVariable::PH, request);
    EXPECT_EQ(decision.status, smarthydro::ControlDecisionStatus::BLOCKED);
    EXPECT_DOUBLE_EQ(decision.command, 0.0);

    request.seconds_since_last_dose = 2000.0;
    request.ph_down_active = true;
    decision = system.execute(smarthydro::ControlledVariable::PH, request);
    EXPECT_EQ(decision.status, smarthydro::ControlDecisionStatus::BLOCKED);

    request.ph_down_active = false;
    request.daily_dose_milliliters = 4.8;
    decision = system.execute(smarthydro::ControlledVariable::PH, request);
    EXPECT_EQ(decision.status, smarthydro::ControlDecisionStatus::LIMITED);
    EXPECT_NEAR(decision.command, 0.2, 1e-12);
}

TEST(RecipeControlSystemTest, PredictiveNutrientsUseModelWaterAndCumulativeDose) {
    smarthydro::RecipeControlSystem system(load_demo_recipe());
    confirm_all(system);
    smarthydro::ControlRequest request;
    request.controller_input.model_estimate = 100.0;
    request.controller_input.water_delivered_liters = 0.5;
    request.controller_input.cumulative_dose_milliliters = 5.0;
    request.seconds_since_last_dose = 4000.0;

    const auto decision =
        system.execute(smarthydro::ControlledVariable::NITROGEN, request);

    EXPECT_NE(decision.status, smarthydro::ControlDecisionStatus::BLOCKED);
    EXPECT_GT(decision.command, 0.0);
    ASSERT_TRUE(decision.predicted_value.has_value());
    EXPECT_LT(*decision.predicted_value, 100.0);
}

TEST(RecipeControlSystemTest, NutrientsIgnoreMeasuredValuesAndRequireValidHistory) {
    smarthydro::RecipeControlSystem system(load_demo_recipe());
    confirm_all(system);
    smarthydro::ControlRequest request;
    request.controller_input.measured_value = 1000.0;
    request.controller_input.model_estimate = 100.0;
    request.seconds_since_last_dose = 4000.0;

    const auto model_decision =
        system.execute(smarthydro::ControlledVariable::NITROGEN, request);

    EXPECT_NE(
        model_decision.status,
        smarthydro::ControlDecisionStatus::BLOCKED);
    EXPECT_GT(model_decision.command, 0.0);
    ASSERT_TRUE(model_decision.predicted_value.has_value());
    EXPECT_DOUBLE_EQ(*model_decision.predicted_value, 100.0);

    request.daily_dose_milliliters = -1.0;
    const auto invalid_history =
        system.execute(smarthydro::ControlledVariable::NITROGEN, request);
    EXPECT_EQ(
        invalid_history.status,
        smarthydro::ControlDecisionStatus::BLOCKED);
    EXPECT_NE(
        invalid_history.message.find("history"),
        std::string::npos);
}

TEST(RecipeControlSystemTest, BlocksInvalidSourcesAndHonorsPhotoperiod) {
    smarthydro::RecipeControlSystem system(load_demo_recipe());
    confirm_all(system);
    smarthydro::ControlRequest request;
    request.controller_input.measured_value = 300.0;
    request.source_valid = false;

    const auto unavailable =
        system.execute(smarthydro::ControlledVariable::LIGHT, request);
    EXPECT_EQ(
        unavailable.status,
        smarthydro::ControlDecisionStatus::BLOCKED);
    EXPECT_EQ(
        unavailable.fault_severity,
        smarthydro::ControlFaultSeverity::RECOVERABLE);

    request.source_valid = true;
    request.controller_input.measured_value = 1300.0;
    request.hour_of_day = 10.0;
    const auto unsafe =
        system.execute(smarthydro::ControlledVariable::LIGHT, request);
    EXPECT_EQ(unsafe.status, smarthydro::ControlDecisionStatus::BLOCKED);
    EXPECT_EQ(
        unsafe.fault_severity,
        smarthydro::ControlFaultSeverity::CRITICAL);
    EXPECT_NE(unsafe.message.find("safety"), std::string::npos);

    request.controller_input.measured_value = 300.0;
    request.hour_of_day = 2.0;
    const auto night =
        system.execute(smarthydro::ControlledVariable::LIGHT, request);
    EXPECT_EQ(night.status, smarthydro::ControlDecisionStatus::APPLIED);
    EXPECT_DOUBLE_EQ(night.command, 0.0);
    EXPECT_EQ(night.message, "outside photoperiod");
}

TEST(RecipeControlSystemTest, RejectsNonIncreasingRecipeReplacementVersion) {
    smarthydro::RecipeControlSystem system(load_demo_recipe());
    auto replacement = load_demo_recipe();

    EXPECT_THROW(
        system.replace_recipe(std::move(replacement)),
        std::invalid_argument);
}

TEST(RecipeControlSystemTest, RecipeReplacementConfirmsFromSelectedStrategyAndAllowsRejection) {
    // Fresh adoption via the constructor already confirms every controller;
    // no confirm_all() setup helper needed.
    smarthydro::RecipeControlSystem system(load_demo_recipe());
    ASSERT_TRUE(system.all_configurations_confirmed());

    auto replacement = load_demo_recipe();
    ++replacement.version;
    replacement.phases.front().targets[0].setpoint = 61.0;

    system.replace_recipe(std::move(replacement));

    // Adopting the replacement recipe (active_recipe_id changing on an
    // already-running zone) confirms straight away too — the Strategy was
    // already decided when the recipe was saved, so it isn't re-litigated
    // per zone that adopts it.
    EXPECT_TRUE(system.all_configurations_confirmed());
    EXPECT_EQ(
        system.recipe().controllers[0].confirmation_state,
        smarthydro::ConfirmationState::CONFIRMED);

    // The manual override path (RejectConfiguration) still works on top of
    // an auto-confirmed configuration — this is the "override a single
    // zone" mechanism the auto-confirm change does not remove.
    system.reject_configuration(
        smarthydro::ControlledVariable::SOIL_MOISTURE);
    EXPECT_EQ(
        system.recipe().controllers[0].confirmation_state,
        smarthydro::ConfirmationState::REJECTED);
}

}  // namespace
