# Aggiunge una demo e la collega sia al simulatore sia al supporto condiviso.
function(add_sensor_experiment target_name source_file)
    add_executable(${target_name} ${source_file})
    target_link_libraries(${target_name}
        PRIVATE
            sensor_simulator
            experiment_common
    )
    target_compile_features(${target_name} PRIVATE cxx_std_17)
    set_target_properties(${target_name} PROPERTIES CXX_EXTENSIONS OFF)
endfunction()

# Il nome del primo argomento e anche il nome dell'eseguibile in build/bin.
add_sensor_experiment(temperature_simulation temperature_simulation.cpp)
add_sensor_experiment(humidity_simulation humidity_simulation.cpp)
add_sensor_experiment(ph_simulation ph_simulation.cpp)
add_sensor_experiment(light_simulation light_simulation.cpp)
