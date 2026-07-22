# La logica condivisa dalle demo e raccolta in una piccola libreria di supporto.
add_library(experiment_common common/experiment_utils.cpp)
target_include_directories(experiment_common
    PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/common
)
target_compile_features(experiment_common PUBLIC cxx_std_17)
set_target_properties(experiment_common PROPERTIES CXX_EXTENSIONS OFF)

# Ogni categoria dispone di una sottocartella pronta ad accogliere le proprie demo.
add_subdirectory(sensors)
add_subdirectory(actuators)
add_subdirectory(controllers)
