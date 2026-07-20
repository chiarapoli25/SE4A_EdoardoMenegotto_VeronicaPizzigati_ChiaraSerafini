cmake_minimum_required(VERSION 3.16)

project(SmartHydroEdge VERSION 0.1.0 LANGUAGES CXX)

add_executable(smarthydro_edge src/main.cpp)

target_compile_features(smarthydro_edge PRIVATE cxx_std_17)
set_target_properties(smarthydro_edge PROPERTIES
    CXX_EXTENSIONS OFF
)
