# Crea la libreria del simulatore degli attuatori.
add_library(actuator_simulator actuator_simulator.cpp)
target_include_directories(actuator_simulator
    PUBLIC ${PROJECT_SOURCE_DIR}/include
)
target_compile_features(actuator_simulator PUBLIC cxx_std_17)
set_target_properties(actuator_simulator PROPERTIES CXX_EXTENSIONS OFF)

# Crea la libreria che possiede lo stato fisico e le dinamiche della serra.
add_library(environment_simulator environment_simulator.cpp)
target_include_directories(environment_simulator
    PUBLIC ${PROJECT_SOURCE_DIR}/include
)
target_compile_features(environment_simulator PUBLIC cxx_std_17)
target_link_libraries(environment_simulator PUBLIC actuator_simulator)
set_target_properties(environment_simulator PROPERTIES CXX_EXTENSIONS OFF)

# Crea la libreria dei sensori, che misura lo stato dell'ambiente senza avanzarlo.
add_library(sensor_simulator sensor_simulator.cpp)
# Rende edge/include disponibile sia a questa libreria sia ai target che la usano.
target_include_directories(sensor_simulator
    PUBLIC ${PROJECT_SOURCE_DIR}/include
)
# Richiede C++17 e disabilita le estensioni specifiche del compilatore.
target_compile_features(sensor_simulator PUBLIC cxx_std_17)
target_link_libraries(sensor_simulator PUBLIC environment_simulator)
set_target_properties(sensor_simulator PROPERTIES CXX_EXTENSIONS OFF)

# Crea la libreria dei controllori.
add_library(controllers controllers.cpp)
target_include_directories(controllers
    PUBLIC ${PROJECT_SOURCE_DIR}/include
)
target_compile_features(controllers PUBLIC cxx_std_17)
set_target_properties(controllers PROPERTIES CXX_EXTENSIONS OFF)

# NOME DELL'ESEGUIBILE PRINCIPALE:
# "edge" e sia il nome del target CMake sia, per impostazione
# predefinita, il nome del file generato in build/bin. Per cambiarlo occorre
# sostituire edge anche nei comandi target_* sottostanti.
add_executable(edge main.cpp)
target_compile_features(edge PRIVATE cxx_std_17)
# Collega all'eseguibile le librerie necessarie.
target_link_libraries(edge
    PRIVATE
        sensor_simulator
        environment_simulator
        actuator_simulator
)
set_target_properties(edge PROPERTIES CXX_EXTENSIONS OFF)

# Se si vuole cambiare solo il nome del file, lasciando invariato il target:
# set_target_properties(edge PROPERTIES OUTPUT_NAME "nuovo_nome")
