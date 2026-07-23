# Supporto comune per CSV e grafici degli experiments.
add_library(experiment_output common/experiment_output.cpp)
target_include_directories(experiment_output PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/common)
target_compile_features(experiment_output PUBLIC cxx_std_17)
set_target_properties(experiment_output PROPERTIES CXX_EXTENSIONS OFF)

# Ogni categoria dispone di una sottocartella per le proprie demo.
add_subdirectory(sensors)
add_subdirectory(actuators)
add_subdirectory(controllers)
