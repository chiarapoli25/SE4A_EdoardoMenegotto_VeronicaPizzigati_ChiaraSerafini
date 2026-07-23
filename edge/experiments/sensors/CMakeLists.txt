# Un solo eseguibile mostra contemporaneamente tutti i sensori.
# "sensor_simulation" e sia il nome del target CMake sia quello del file
# creato in build/bin; per rinominarlo occorre modificare il primo argomento.
add_executable(sensor_simulation sensor_simulation.cpp)
target_link_libraries(sensor_simulation
    PRIVATE
        sensor_simulator
)
target_compile_features(sensor_simulation PRIVATE cxx_std_17)
set_target_properties(sensor_simulation PROPERTIES CXX_EXTENSIONS OFF)
