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

# Test di ricette, Strategy, conferma e limiti prioritari.
add_executable(control_system_tests control_system_tests.cpp)
target_link_libraries(control_system_tests
    PRIVATE
        recipe_json
        GTest::gtest_main
)
target_compile_features(control_system_tests PRIVATE cxx_std_17)
target_compile_definitions(control_system_tests
    PRIVATE
        SMARTHYDRO_EXAMPLE_RECIPE_PATH="${PROJECT_SOURCE_DIR}/../config/example_recipe.json"
)
set_target_properties(control_system_tests PROPERTIES CXX_EXTENSIONS OFF)
gtest_discover_tests(control_system_tests)

# Test del ciclo operativo completo guidato dalla ricetta.
add_executable(edge_runtime_tests edge_runtime_tests.cpp)
target_link_libraries(edge_runtime_tests
    PRIVATE
        edge_runtime
        recipe_json
        GTest::gtest_main
)
target_compile_features(edge_runtime_tests PRIVATE cxx_std_17)
target_compile_definitions(edge_runtime_tests
    PRIVATE
        SMARTHYDRO_EXAMPLE_RECIPE_PATH="${PROJECT_SOURCE_DIR}/../config/example_recipe.json"
)
set_target_properties(edge_runtime_tests PROPERTIES CXX_EXTENSIONS OFF)
gtest_discover_tests(edge_runtime_tests)

# Il main deve caricare la ricetta e completare almeno un ciclo senza servizi
# esterni o interazione utente.
add_test(
    NAME edge_main_recipe_smoke
    COMMAND $<TARGET_FILE:edge>
        --recipe "${PROJECT_SOURCE_DIR}/../config/example_recipe.json"
        --steps 1
        --step-seconds 900
)
set_tests_properties(
    edge_main_recipe_smoke
    PROPERTIES
        PASS_REGULAR_EXPRESSION "Status: recipe runtime ready"
)

# Smoke test dei programmi dimostrativi. Sensori e ricette creano un solo CSV
# e nessun PNG; gli altri output di test finiscono esclusivamente nella build.
if(BUILD_EXPERIMENTS)
    find_program(GNUPLOT_EXECUTABLE gnuplot)
    if(GNUPLOT_EXECUTABLE)
        add_test(
            NAME experiment_sensor_simulation_day
            COMMAND ${CMAKE_COMMAND}
                -DEXPERIMENT_EXECUTABLE=$<TARGET_FILE:sensor_simulation>
                -DOUTPUT_DIRECTORY=${CMAKE_CURRENT_BINARY_DIR}/smoke/sensor_day
                -DDURATION_CHOICE=1
                -DSOIL_CHOICE=1
                -DEXPECTED_SAMPLES=97
                -P ${CMAKE_CURRENT_SOURCE_DIR}/sensor_simulation_smoke.cmake
        )
        add_test(
            NAME experiment_sensor_simulation_week
            COMMAND ${CMAKE_COMMAND}
                -DEXPERIMENT_EXECUTABLE=$<TARGET_FILE:sensor_simulation>
                -DOUTPUT_DIRECTORY=${CMAKE_CURRENT_BINARY_DIR}/smoke/sensor_week
                -DDURATION_CHOICE=2
                -DSOIL_CHOICE=2
                -DEXPECTED_SAMPLES=673
                -P ${CMAKE_CURRENT_SOURCE_DIR}/sensor_simulation_smoke.cmake
        )
    endif()

    if(GNUPLOT_EXECUTABLE)
        add_test(
            NAME experiment_actuator_simulation
            COMMAND ${CMAKE_COMMAND}
                -DEXPERIMENT_EXECUTABLE=$<TARGET_FILE:actuator_simulation>
                -DOUTPUT_DIRECTORY=${CMAKE_CURRENT_BINARY_DIR}/smoke/actuator_simulation
                -P ${CMAKE_CURRENT_SOURCE_DIR}/actuator_simulation_smoke.cmake
        )
        add_test(
            NAME experiment_controller_irrigation_comparison
            COMMAND ${CMAKE_COMMAND}
                -DEXPERIMENT_EXECUTABLE=$<TARGET_FILE:controller_irrigation_comparison>
                -DOUTPUT_DIRECTORY=${CMAKE_CURRENT_BINARY_DIR}/smoke/controller_irrigation_comparison
                -P ${CMAKE_CURRENT_SOURCE_DIR}/controller_comparison_smoke.cmake
        )
        add_test(
            NAME experiment_controller_generic_comparison
            COMMAND ${CMAKE_COMMAND}
                -DEXPERIMENT_EXECUTABLE=$<TARGET_FILE:controller_generic_comparison>
                -DOUTPUT_DIRECTORY=${CMAKE_CURRENT_BINARY_DIR}/smoke/controller_generic_comparison
                -P ${CMAKE_CURRENT_SOURCE_DIR}/controller_comparison_smoke.cmake
        )
    endif()
endif()

# Per cambiare solo il nome di un file senza rinominare il target CMake:
# set_target_properties(sensor_tests PROPERTIES OUTPUT_NAME "nuovo_nome")
