#include <smarthydro/control/controllers.hpp>

#include <stdexcept>

#include <gtest/gtest.h>

namespace {

TEST(ThresholdControllerTest, UsesHysteresisForIncreasingProcess) {
    smarthydro::ThresholdController controller(
        40.0,
        60.0,
        smarthydro::ControlDirection::INCREASES_PROCESS_VALUE,
        0.5,
        0.0);

    EXPECT_DOUBLE_EQ(controller.update(35.0), 0.5);
    EXPECT_DOUBLE_EQ(controller.update(50.0), 0.5);
    EXPECT_DOUBLE_EQ(controller.update(65.0), 0.0);
    EXPECT_DOUBLE_EQ(controller.update(50.0), 0.0);
}

TEST(ThresholdControllerTest, SupportsDecreasingProcess) {
    smarthydro::ThresholdController controller(
        40.0,
        60.0,
        smarthydro::ControlDirection::DECREASES_PROCESS_VALUE);

    EXPECT_DOUBLE_EQ(controller.update(65.0), 100.0);
    EXPECT_DOUBLE_EQ(controller.update(50.0), 100.0);
    EXPECT_DOUBLE_EQ(controller.update(35.0), 0.0);
}

TEST(ThresholdControllerTest, RejectsInvalidBand) {
    EXPECT_THROW(smarthydro::ThresholdController(60.0, 40.0), std::invalid_argument);
}

TEST(PidControllerTest, AppliesProportionalTermAndCommandLimits) {
    smarthydro::PidController controller({
        50.0,
        2.0,
        0.0,
        0.0,
        {0.0, 100.0},
        smarthydro::ControlDirection::INCREASES_PROCESS_VALUE,
    });

    EXPECT_DOUBLE_EQ(controller.update(40.0, 1.0), 20.0);
    EXPECT_DOUBLE_EQ(controller.update(-10.0, 1.0), 100.0);
    EXPECT_DOUBLE_EQ(controller.update(60.0, 1.0), 0.0);
}

TEST(PidControllerTest, AccumulatesIntegralAndCanReset) {
    smarthydro::PidController controller({
        10.0,
        0.0,
        1.0,
        0.0,
        {0.0, 100.0},
        smarthydro::ControlDirection::INCREASES_PROCESS_VALUE,
    });

    EXPECT_DOUBLE_EQ(controller.update(9.0, 1.0), 1.0);
    EXPECT_DOUBLE_EQ(controller.update(9.0, 1.0), 2.0);
    controller.reset();
    EXPECT_DOUBLE_EQ(controller.update(9.0, 1.0), 1.0);
}

TEST(PidControllerTest, OneSidedIntegralCanUnwindAfterOvershoot) {
    smarthydro::PidController controller({
        10.0,
        2.0,
        1.0,
        0.0,
        {0.0, 100.0},
        smarthydro::ControlDirection::INCREASES_PROCESS_VALUE,
    });

    // Accumula un termine integrale positivo mentre la misura e' bassa.
    EXPECT_DOUBLE_EQ(controller.update(9.0, 1.0), 3.0);
    // Dopo l'overshoot il comando resta correttamente al limite inferiore,
    // ma l'integrale deve potersi scaricare fino a zero.
    EXPECT_DOUBLE_EQ(controller.update(11.0, 1.0), 0.0);
    EXPECT_DOUBLE_EQ(controller.update(10.0, 1.0), 0.0);
}

TEST(PidControllerTest, SupportsDecreasingProcess) {
    smarthydro::PidController controller({
        50.0,
        2.0,
        0.0,
        0.0,
        {0.0, 100.0},
        smarthydro::ControlDirection::DECREASES_PROCESS_VALUE,
    });

    EXPECT_DOUBLE_EQ(controller.update(60.0, 1.0), 20.0);
}

TEST(PidControllerTest, RejectsInvalidConfigurationAndTimeStep) {
    const smarthydro::PidConfig invalid_config{
        0.0,
        -1.0,
        0.0,
        0.0,
        {0.0, 100.0},
        smarthydro::ControlDirection::INCREASES_PROCESS_VALUE,
    };
    EXPECT_THROW(
        static_cast<void>(smarthydro::PidController{invalid_config}),
        std::invalid_argument);

    smarthydro::PidController controller({
        0.0,
        1.0,
        0.0,
        0.0,
        {0.0, 100.0},
        smarthydro::ControlDirection::INCREASES_PROCESS_VALUE,
    });
    EXPECT_THROW(controller.update(1.0, 0.0), std::invalid_argument);
}

TEST(PredictiveControllerTest, UsesMeasuredTrend) {
    smarthydro::PredictiveController controller({
        50.0,
        2.0,
        2.0,
        10.0,
        {0.0, 100.0},
        smarthydro::ControlDirection::INCREASES_PROCESS_VALUE,
    });

    const auto first = controller.update(45.0);
    EXPECT_DOUBLE_EQ(first.measured_trend, 0.0);
    EXPECT_DOUBLE_EQ(first.predicted_value, 45.0);
    EXPECT_DOUBLE_EQ(first.command, 20.0);

    const auto second = controller.update(47.0);
    EXPECT_DOUBLE_EQ(second.measured_trend, 2.0);
    EXPECT_DOUBLE_EQ(second.predicted_value, 51.0);
    EXPECT_DOUBLE_EQ(second.command, 8.0);
}

TEST(PredictiveControllerTest, ResetClearsPreviousMeasurement) {
    smarthydro::PredictiveController controller({
        50.0,
        2.0,
        2.0,
        10.0,
        {0.0, 100.0},
        smarthydro::ControlDirection::INCREASES_PROCESS_VALUE,
    });

    controller.update(45.0);
    controller.update(47.0);
    controller.reset();

    EXPECT_DOUBLE_EQ(controller.update(47.0).measured_trend, 0.0);
}

TEST(PredictiveControllerTest, ComputesNutrientDoseFromMassBalance) {
    smarthydro::PredictiveController controller({
        40.0,
        0.0,
        0.01,
        0.0,
        {0.0, 5.0},
        smarthydro::ControlDirection::INCREASES_PROCESS_VALUE,
    });
    smarthydro::ControllerInput input;
    input.model_estimate = 40.0;
    input.delta_time_seconds = 900.0;
    input.root_water_volume_liters = 2.0;
    input.reference_root_water_volume_liters = 3.0;
    input.retained_irrigation_liters = 1.0;
    input.nutrient_milligrams_per_command_unit = 20.0;

    const auto result = controller.compute(input);

    ASSERT_TRUE(result.valid);
    ASSERT_TRUE(result.predicted_value.has_value());
    // 80 mg presenti + 40 mg richiesti per portare 3 L a 40 mg/L:
    // con un prodotto da 20 mg/mL servono esattamente 2 mL.
    EXPECT_NEAR(result.command, 2.0, 1.0e-12);
    EXPECT_NEAR(*result.predicted_value, 80.0 / 3.0, 1.0e-12);
}

TEST(PredictiveControllerTest, TargetsMassAtNominalRecipeMoisture) {
    smarthydro::PredictiveController controller({
        40.0,
        0.0,
        0.01,
        0.0,
        {0.0, 5.0},
        smarthydro::ControlDirection::INCREASES_PROCESS_VALUE,
    });
    smarthydro::ControllerInput input;
    input.model_estimate = 40.0;
    input.delta_time_seconds = 900.0;
    input.root_water_volume_liters = 2.0;
    input.reference_root_water_volume_liters = 2.5;
    input.retained_irrigation_liters = 1.0;
    input.nutrient_milligrams_per_command_unit = 20.0;

    const auto result = controller.compute(input);

    ASSERT_TRUE(result.valid);
    // Il volume post-irrigazione e' 3 L, ma il setpoint e' definito ai 2.5 L
    // nominali: massa obiettivo 100 mg, quindi basta aggiungerne 20 = 1 mL.
    EXPECT_NEAR(result.command, 1.0, 1.0e-12);
}

TEST(PredictiveControllerTest, RejectsInvalidNutrientMassContext) {
    smarthydro::PredictiveController controller({
        40.0,
        0.0,
        0.01,
        0.0,
        {0.0, 5.0},
        smarthydro::ControlDirection::INCREASES_PROCESS_VALUE,
    });
    smarthydro::ControllerInput input;
    input.model_estimate = 30.0;
    input.root_water_volume_liters = 0.0;
    input.nutrient_milligrams_per_command_unit = 20.0;

    EXPECT_FALSE(controller.compute(input).valid);
}

TEST(PredictiveControllerTest, RejectsNegativePredictionHorizon) {
    const smarthydro::PredictiveConfig invalid_config{
        0.0,
        -1.0,
        1.0,
        0.0,
        {0.0, 100.0},
        smarthydro::ControlDirection::INCREASES_PROCESS_VALUE,
    };

    EXPECT_THROW(
        static_cast<void>(smarthydro::PredictiveController{invalid_config}),
        std::invalid_argument);
}

}  // namespace
