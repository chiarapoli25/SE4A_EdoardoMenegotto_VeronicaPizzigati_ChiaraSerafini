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

add_library(smarthydro_controllers src/controllers.cpp)
target_include_directories(smarthydro_controllers
    PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/include
)
target_compile_features(smarthydro_controllers PUBLIC cxx_std_17)
set_target_properties(smarthydro_controllers PROPERTIES CXX_EXTENSIONS OFF)

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
    include(FetchContent)
    FetchContent_Declare(
        googletest
        URL https://github.com/google/googletest/archive/refs/tags/v1.15.2.tar.gz
        DOWNLOAD_EXTRACT_TIMESTAMP TRUE
    )
    set(BUILD_GMOCK OFF CACHE BOOL "" FORCE)
    set(INSTALL_GTEST OFF CACHE BOOL "" FORCE)
    FetchContent_MakeAvailable(googletest)
    include(GoogleTest)

    add_executable(smarthydro_sensor_tests tests/sensor_simulator_tests.cpp)
    target_link_libraries(smarthydro_sensor_tests
        PRIVATE
            smarthydro_sensor_simulator
            GTest::gtest_main
    )
    target_compile_features(smarthydro_sensor_tests PRIVATE cxx_std_17)
    set_target_properties(smarthydro_sensor_tests PROPERTIES CXX_EXTENSIONS OFF)
    gtest_discover_tests(smarthydro_sensor_tests)

    add_executable(smarthydro_actuator_tests tests/actuator_simulator_tests.cpp)
    target_link_libraries(smarthydro_actuator_tests
        PRIVATE
            smarthydro_actuator_simulator
            GTest::gtest_main
    )
    target_compile_features(smarthydro_actuator_tests PRIVATE cxx_std_17)
    set_target_properties(smarthydro_actuator_tests PROPERTIES CXX_EXTENSIONS OFF)
    gtest_discover_tests(smarthydro_actuator_tests)

    add_executable(smarthydro_controller_tests tests/controllers_tests.cpp)
    target_link_libraries(smarthydro_controller_tests
        PRIVATE
            smarthydro_controllers
            GTest::gtest_main
    )
    target_compile_features(smarthydro_controller_tests PRIVATE cxx_std_17)
    set_target_properties(smarthydro_controller_tests PROPERTIES CXX_EXTENSIONS OFF)
    gtest_discover_tests(smarthydro_controller_tests)
endif()
