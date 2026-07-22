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
add_executable(sensor_tests sensor_simulator_tests.cpp)
# Collega il codice da verificare e il main fornito da GoogleTest.
target_link_libraries(sensor_tests
    PRIVATE
        smarthydro_sensor_simulator
        GTest::gtest_main
)
target_compile_features(sensor_tests PRIVATE cxx_std_17)
set_target_properties(sensor_tests PROPERTIES CXX_EXTENSIONS OFF)
gtest_discover_tests(sensor_tests)

# Eseguibile dei test del simulatore degli attuatori.
add_executable(actuator_tests actuator_simulator_tests.cpp)
target_link_libraries(actuator_tests
    PRIVATE
        smarthydro_actuator_simulator
        GTest::gtest_main
)
target_compile_features(actuator_tests PRIVATE cxx_std_17)
set_target_properties(actuator_tests PROPERTIES CXX_EXTENSIONS OFF)
gtest_discover_tests(actuator_tests)

# Eseguibile dei test dei controllori.
add_executable(controller_tests controllers_tests.cpp)
target_link_libraries(controller_tests
    PRIVATE
        smarthydro_controllers
        GTest::gtest_main
)
target_compile_features(controller_tests PRIVATE cxx_std_17)
set_target_properties(controller_tests PROPERTIES CXX_EXTENSIONS OFF)
gtest_discover_tests(controller_tests)

# Per cambiare solo il nome di un file senza rinominare il target CMake:
# set_target_properties(smarthydro_sensor_tests PROPERTIES OUTPUT_NAME "nuovo_nome")
