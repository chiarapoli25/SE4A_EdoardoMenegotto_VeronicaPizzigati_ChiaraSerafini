# Il runner condiviso evita di duplicare il flusso interattivo nelle demo.
add_library(sensor_experiment_runner ../common/sensor_experiment_runner.cpp)
target_include_directories(sensor_experiment_runner
    PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/../common
)
target_compile_features(sensor_experiment_runner PUBLIC cxx_std_17)
target_link_libraries(sensor_experiment_runner
    PUBLIC
        sensor_simulator
        environment_simulator
        actuator_simulator
)
set_target_properties(sensor_experiment_runner PROPERTIES CXX_EXTENSIONS OFF)

# Aggiunge una demo e la collega al runner dei sensori.
function(add_sensor_experiment target_name source_file)
    add_executable(${target_name} ${source_file})
    target_link_libraries(${target_name}
        PRIVATE
            sensor_experiment_runner
    )
    target_compile_features(${target_name} PRIVATE cxx_std_17)
    set_target_properties(${target_name} PROPERTIES CXX_EXTENSIONS OFF)
endfunction()

# Il nome del primo argomento e anche il nome dell'eseguibile in build/bin.
add_sensor_experiment(temperature_simulation temperature_simulation.cpp)
add_sensor_experiment(humidity_simulation humidity_simulation.cpp)
add_sensor_experiment(ph_simulation ph_simulation.cpp)
add_sensor_experiment(light_simulation light_simulation.cpp)
add_sensor_experiment(environment_simulation environment_simulation.cpp)
