#include <smarthydro/control/dli_accumulator.hpp>

#include <cmath>
#include <stdexcept>
#include <string>

namespace smarthydro {
namespace {

constexpr double kMicromolesPerMole = 1.0e6;

void require_finite(double value, const char* name) {
    if (!std::isfinite(value)) {
        throw std::invalid_argument(std::string(name) + " must be finite");
    }
}

}  // namespace

void DliAccumulator::integrate(
    double ppfd_umol_m2_s,
    double delta_time_seconds) {
    require_finite(ppfd_umol_m2_s, "ppfd_umol_m2_s");
    require_finite(delta_time_seconds, "delta_time_seconds");
    if (ppfd_umol_m2_s < 0.0) {
        throw std::invalid_argument("ppfd_umol_m2_s must not be negative");
    }
    if (delta_time_seconds <= 0.0) {
        throw std::invalid_argument("delta_time_seconds must be positive");
    }
    value_mol_m2_ +=
        ppfd_umol_m2_s * delta_time_seconds / kMicromolesPerMole;
}

void DliAccumulator::reset() noexcept {
    value_mol_m2_ = 0.0;
}

double DliAccumulator::value_mol_m2() const noexcept {
    return value_mol_m2_;
}

}  // namespace smarthydro
