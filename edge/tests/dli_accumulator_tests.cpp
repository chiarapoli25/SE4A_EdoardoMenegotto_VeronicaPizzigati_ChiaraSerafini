#include <smarthydro/control/dli_accumulator.hpp>

#include <limits>
#include <stdexcept>

#include <gtest/gtest.h>

TEST(DliAccumulatorTest, StartsAtZero) {
    smarthydro::DliAccumulator accumulator;
    EXPECT_DOUBLE_EQ(accumulator.value_mol_m2(), 0.0);
}

TEST(DliAccumulatorTest, IntegratesPpfdOverTimeIntoMolPerSquareMeter) {
    smarthydro::DliAccumulator accumulator;
    // 500 umol/(m2 s) per 3600s = 500*3600/1e6 = 1.8 mol/m^2.
    accumulator.integrate(500.0, 3600.0);
    EXPECT_DOUBLE_EQ(accumulator.value_mol_m2(), 1.8);
}

TEST(DliAccumulatorTest, AccumulatesAcrossMultipleSteps) {
    smarthydro::DliAccumulator accumulator;
    for (int i = 0; i < 4; ++i) {
        accumulator.integrate(300.0, 900.0);
    }
    // 300 * 900 * 4 / 1e6 = 1.08 mol/m^2.
    EXPECT_NEAR(accumulator.value_mol_m2(), 1.08, 1e-9);
}

TEST(DliAccumulatorTest, ZeroPpfdDoesNotChangeTheAccumulatedValue) {
    smarthydro::DliAccumulator accumulator;
    accumulator.integrate(400.0, 900.0);
    const double before = accumulator.value_mol_m2();
    accumulator.integrate(0.0, 900.0);
    EXPECT_DOUBLE_EQ(accumulator.value_mol_m2(), before);
}

TEST(DliAccumulatorTest, ResetReturnsToZeroRegardlessOfPriorAccumulation) {
    smarthydro::DliAccumulator accumulator;
    accumulator.integrate(600.0, 3600.0);
    accumulator.integrate(600.0, 3600.0);
    ASSERT_GT(accumulator.value_mol_m2(), 0.0);

    accumulator.reset();

    EXPECT_DOUBLE_EQ(accumulator.value_mol_m2(), 0.0);
}

TEST(DliAccumulatorTest, CanAccumulateAgainAfterReset) {
    smarthydro::DliAccumulator accumulator;
    accumulator.integrate(600.0, 3600.0);
    accumulator.reset();

    accumulator.integrate(500.0, 3600.0);

    EXPECT_DOUBLE_EQ(accumulator.value_mol_m2(), 1.8);
}

TEST(DliAccumulatorTest, RejectsNegativePpfd) {
    smarthydro::DliAccumulator accumulator;
    EXPECT_THROW(accumulator.integrate(-1.0, 900.0), std::invalid_argument);
}

TEST(DliAccumulatorTest, RejectsNonPositiveDuration) {
    smarthydro::DliAccumulator accumulator;
    EXPECT_THROW(accumulator.integrate(400.0, 0.0), std::invalid_argument);
    EXPECT_THROW(accumulator.integrate(400.0, -900.0), std::invalid_argument);
}

TEST(DliAccumulatorTest, RejectsNonFiniteInputs) {
    smarthydro::DliAccumulator accumulator;
    const double infinity = std::numeric_limits<double>::infinity();
    const double nan = std::numeric_limits<double>::quiet_NaN();
    EXPECT_THROW(accumulator.integrate(infinity, 900.0), std::invalid_argument);
    EXPECT_THROW(accumulator.integrate(400.0, nan), std::invalid_argument);
}

TEST(DliAccumulatorTest, ValueNeverGoesNegative) {
    // Non esiste un ingresso valido che possa spingere il DLI sotto zero
    // (integrate() rifiuta PPFD negativi, reset() lo riporta esattamente a
    // zero): questo test documenta l'invariante, non lo forza artificialmente.
    smarthydro::DliAccumulator accumulator;
    for (int i = 0; i < 100; ++i) {
        accumulator.integrate(0.0, 900.0);
        EXPECT_GE(accumulator.value_mol_m2(), 0.0);
    }
}
