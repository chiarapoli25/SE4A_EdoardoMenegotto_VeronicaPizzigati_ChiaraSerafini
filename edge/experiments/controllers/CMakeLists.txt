# Confronta i tre controllori in anello chiuso sullo stesso problema di
# irrigazione. I dati vengono inviati direttamente a gnuplot senza creare file.
add_executable(
    controller_irrigation_comparison
    controller_irrigation_comparison.cpp
)
target_link_libraries(
    controller_irrigation_comparison
    PRIVATE
        controllers
        sensor_simulator
        environment_simulator
        actuator_simulator
)
target_compile_features(
    controller_irrigation_comparison
    PRIVATE cxx_std_17
)
set_target_properties(
    controller_irrigation_comparison
    PROPERTIES CXX_EXTENSIONS OFF
)

# Isola il comportamento degli algoritmi su un processo normalizzato comune.
add_executable(
    controller_generic_comparison
    controller_generic_comparison.cpp
)
target_link_libraries(
    controller_generic_comparison
    PRIVATE controllers
)
target_compile_features(
    controller_generic_comparison
    PRIVATE cxx_std_17
)
set_target_properties(
    controller_generic_comparison
    PROPERTIES CXX_EXTENSIONS OFF
)
