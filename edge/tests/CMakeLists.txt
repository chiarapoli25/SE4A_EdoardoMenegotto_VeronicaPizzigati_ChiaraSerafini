# FetchContent permette a CMake di scaricare e integrare dipendenze esterne.
include(FetchContent)

# Dichiara la versione precisa di GoogleTest utilizzata dal progetto.
FetchContent_Declare(
    googletest
    URL https://github.com/google/googletest/archive/refs/tags/v1.15.2.tar.gz
    DOWNLOAD_EXTRACT_TIMESTAMP TRUE
)
# Non servono GoogleMock ne l'installazione globale di GoogleTest.
set(BUILD_GMOCK OFF CACHE BOOL "" FORCE)
set(INSTALL_GTEST OFF CACHE BOOL "" FORCE)
# Scarica GoogleTest, se necessario, e ne rende disponibili i target.
FetchContent_MakeAvailable(googletest)

# Importa gtest_discover_tests, che registra i singoli test in CTest.
include(GoogleTest)

# NOMI DEGLI ESEGUIBILI DI TEST:
# Il primo argomento di ogni add_executable e il nome del target e, per
# impostazione predefinita, del file generato in build/bin. Se lo si cambia,
# bisogna usare il nuovo nome anche nei successivi comandi target_* e
# gtest_discover_tests relativi a quel target.

# Eseguibile dei test del simulatore dei sensori.
add_executable(sensor_tests sensor_tests.cpp)
# Collega il codice da verificare e il main fornito da GoogleTest.
target_link_libraries(sensor_tests
    PRIVATE
        sensor_simulator
        GTest::gtest_main
)
target_compile_features(sensor_tests PRIVATE cxx_std_17)
set_target_properties(sensor_tests PROPERTIES CXX_EXTENSIONS OFF)
gtest_discover_tests(sensor_tests)

# Eseguibile dei test delle dinamiche fisiche dell'ambiente.
add_executable(environment_tests environment_tests.cpp)
target_link_libraries(environment_tests
    PRIVATE
        environment_simulator
        GTest::gtest_main
)
target_compile_features(environment_tests PRIVATE cxx_std_17)
set_target_properties(environment_tests PROPERTIES CXX_EXTENSIONS OFF)
gtest_discover_tests(environment_tests)

# Eseguibile dei test del simulatore degli attuatori.
add_executable(actuator_tests actuator_tests.cpp)
target_link_libraries(actuator_tests
    PRIVATE
        actuator_simulator
        GTest::gtest_main
)
target_compile_features(actuator_tests PRIVATE cxx_std_17)
set_target_properties(actuator_tests PROPERTIES CXX_EXTENSIONS OFF)
gtest_discover_tests(actuator_tests)

# Eseguibile dei test dei controllori.
add_executable(controller_tests controllers_tests.cpp)
target_link_libraries(controller_tests
    PRIVATE
        controllers
        GTest::gtest_main
)
target_compile_features(controller_tests PRIVATE cxx_std_17)
set_target_properties(controller_tests PROPERTIES CXX_EXTENSIONS OFF)
gtest_discover_tests(controller_tests)

# Smoke test dei programmi dimostrativi. I risultati finiscono esclusivamente
# nella build e non toccano i CSV/PNG versionati in experiment_results.
if(BUILD_EXPERIMENTS)
    set(EXPERIMENT_SMOKE_SCRIPT "${CMAKE_CURRENT_SOURCE_DIR}/experiment_smoke.cmake")

    add_test(
        NAME experiment_light_day_natural_without_gnuplot
        COMMAND ${CMAKE_COMMAND}
            -DEXPERIMENT_EXECUTABLE=$<TARGET_FILE:light_simulation>
            -DOUTPUT_DIRECTORY=${CMAKE_CURRENT_BINARY_DIR}/smoke/light
            -DBASE_NAME=light_day_natural_soil-aerated-universal_seed-31415
            -DEXPECTED_LINES=98
            -DDURATION=day
            -DSOIL=aerated-universal
            -DNO_GNUPLOT=ON
            -DCHECK_DARKNESS=ON
            -P ${EXPERIMENT_SMOKE_SCRIPT}
    )

    add_test(
        NAME experiment_humidity_week_without_actuators
        COMMAND ${CMAKE_COMMAND}
            -DEXPERIMENT_EXECUTABLE=$<TARGET_FILE:humidity_simulation>
            -DOUTPUT_DIRECTORY=${CMAKE_CURRENT_BINARY_DIR}/smoke/humidity
            -DBASE_NAME=humidity_week_natural_soil-draining_seed-31415
            -DEXPECTED_LINES=674
            -DDURATION=week
            -DSOIL=draining
            -DNO_GNUPLOT=ON
            -P ${EXPERIMENT_SMOKE_SCRIPT}
    )

    add_test(
        NAME experiment_soil_moisture_day_without_actuators
        COMMAND ${CMAKE_COMMAND}
            -DEXPERIMENT_EXECUTABLE=$<TARGET_FILE:soil_moisture_simulation>
            -DOUTPUT_DIRECTORY=${CMAKE_CURRENT_BINARY_DIR}/smoke/soil_moisture
            -DBASE_NAME=soil_moisture_day_natural_soil-aerated-universal_seed-31415
            -DEXPECTED_LINES=98
            -DDURATION=day
            -DSOIL=aerated-universal
            -DNO_GNUPLOT=ON
            -P ${EXPERIMENT_SMOKE_SCRIPT}
    )

    find_program(GNUPLOT_EXECUTABLE gnuplot)
    if(GNUPLOT_EXECUTABLE)
        add_test(
            NAME experiment_environment_generates_png
            COMMAND ${CMAKE_COMMAND}
                -DEXPERIMENT_EXECUTABLE=$<TARGET_FILE:environment_simulation>
                -DOUTPUT_DIRECTORY=${CMAKE_CURRENT_BINARY_DIR}/smoke/environment
                -DBASE_NAME=environment_day_natural_soil-organic-retentive_seed-31415
                -DEXPECTED_LINES=98
                -DDURATION=day
                -DSOIL=organic-retentive
                -DEXPECT_PNG=ON
                -P ${EXPERIMENT_SMOKE_SCRIPT}
        )
    endif()

    set(STANDARD_EXPERIMENT_SMOKE_SCRIPT
        "${CMAKE_CURRENT_SOURCE_DIR}/standard_experiment_smoke.cmake")
    if(GNUPLOT_EXECUTABLE)
        set(STANDARD_EXPECT_PNG ON)
    else()
        set(STANDARD_EXPECT_PNG OFF)
    endif()

    function(add_standard_experiment_smoke_test test_name target_name base_name line_count)
        add_test(
            NAME ${test_name}
            COMMAND ${CMAKE_COMMAND}
                -DEXPERIMENT_EXECUTABLE=$<TARGET_FILE:${target_name}>
                -DOUTPUT_DIRECTORY=${CMAKE_CURRENT_BINARY_DIR}/smoke/${target_name}
                -DBASE_NAME=${base_name}
                -DEXPECTED_LINES=${line_count}
                -DEXPECT_PNG=${STANDARD_EXPECT_PNG}
                -P ${STANDARD_EXPERIMENT_SMOKE_SCRIPT}
        )
    endfunction()

    if(GNUPLOT_EXECUTABLE)
        add_test(
            NAME experiment_actuator_simulation
            COMMAND ${CMAKE_COMMAND}
                -DEXPERIMENT_EXECUTABLE=$<TARGET_FILE:actuator_simulation>
                -DOUTPUT_DIRECTORY=${CMAKE_CURRENT_BINARY_DIR}/smoke/actuator_simulation
                -P ${CMAKE_CURRENT_SOURCE_DIR}/actuator_simulation_smoke.cmake
        )
    endif()

    add_standard_experiment_smoke_test(
        experiment_threshold_step_response
        threshold_step_response
        threshold_step_response
        25)
    add_standard_experiment_smoke_test(
        experiment_pid_step_response
        pid_step_response
        pid_step_response
        36)
    add_standard_experiment_smoke_test(
        experiment_predictive_step_response
        predictive_step_response
        predictive_step_response
        26)
endif()

# Per cambiare solo il nome di un file senza rinominare il target CMake:
# set_target_properties(sensor_tests PROPERTIES OUTPUT_NAME "nuovo_nome")
