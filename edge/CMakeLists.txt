cmake_minimum_required(VERSION 3.16)

project(SmartHydroEdge VERSION 0.1.0 LANGUAGES CXX)

add_library(smarthydro_sensor_simulator src/sensor_simulator.cpp)
target_include_directories(smarthydro_sensor_simulator
    PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/include
)
target_compile_features(smarthydro_sensor_simulator PUBLIC cxx_std_17)
set_target_properties(smarthydro_sensor_simulator PROPERTIES CXX_EXTENSIONS OFF)

add_library(smarthydro_actuator_simulator src/actuator_simulator.cpp)
target_include_directories(smarthydro_actuator_simulator
    PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/include
)
target_compile_features(smarthydro_actuator_simulator PUBLIC cxx_std_17)
set_target_properties(smarthydro_actuator_simulator PROPERTIES CXX_EXTENSIONS OFF)

add_executable(smarthydro_edge src/main.cpp)
target_compile_features(smarthydro_edge PRIVATE cxx_std_17)
target_link_libraries(smarthydro_edge
    PRIVATE
        smarthydro_sensor_simulator
        smarthydro_actuator_simulator
)
set_target_properties(smarthydro_edge PROPERTIES CXX_EXTENSIONS OFF)

include(CTest)

if(BUILD_TESTING)
    add_executable(smarthydro_sensor_tests tests/sensor_simulator_tests.cpp)
    target_link_libraries(smarthydro_sensor_tests PRIVATE smarthydro_sensor_simulator)
    target_compile_features(smarthydro_sensor_tests PRIVATE cxx_std_17)
    set_target_properties(smarthydro_sensor_tests PROPERTIES CXX_EXTENSIONS OFF)

    add_test(NAME smarthydro_sensor_tests COMMAND smarthydro_sensor_tests)

    add_executable(smarthydro_actuator_tests tests/actuator_simulator_tests.cpp)
    target_link_libraries(smarthydro_actuator_tests PRIVATE smarthydro_actuator_simulator)
    target_compile_features(smarthydro_actuator_tests PRIVATE cxx_std_17)
    set_target_properties(smarthydro_actuator_tests PROPERTIES CXX_EXTENSIONS OFF)

    add_test(NAME smarthydro_actuator_tests COMMAND smarthydro_actuator_tests)
endif()
