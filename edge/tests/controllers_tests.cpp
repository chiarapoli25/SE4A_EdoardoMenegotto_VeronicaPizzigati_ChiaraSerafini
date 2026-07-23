#include "smarthydro/controllers.hpp"

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
