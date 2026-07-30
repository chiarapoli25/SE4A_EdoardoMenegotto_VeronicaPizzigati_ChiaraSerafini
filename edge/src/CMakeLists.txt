# Crea la libreria del simulatore degli attuatori.
add_library(
    actuator_simulator
    simulation/actuator_simulator.cpp
)
target_include_directories(actuator_simulator
    PUBLIC ${PROJECT_SOURCE_DIR}/include
)
target_compile_features(actuator_simulator PUBLIC cxx_std_17)
set_target_properties(actuator_simulator PROPERTIES CXX_EXTENSIONS OFF)

# Crea la libreria che possiede lo stato fisico e le dinamiche della serra.
add_library(
    environment_simulator
    simulation/environment_simulator.cpp
)
target_include_directories(environment_simulator
    PUBLIC ${PROJECT_SOURCE_DIR}/include
)
target_compile_features(environment_simulator PUBLIC cxx_std_17)
target_link_libraries(environment_simulator PUBLIC actuator_simulator)
set_target_properties(environment_simulator PROPERTIES CXX_EXTENSIONS OFF)

# Crea la libreria dei sensori, che misura lo stato dell'ambiente senza avanzarlo.
add_library(
    sensor_simulator
    simulation/sensor_simulator.cpp
)
# Rende edge/include disponibile sia a questa libreria sia ai target che la usano.
target_include_directories(sensor_simulator
    PUBLIC ${PROJECT_SOURCE_DIR}/include
)
# Richiede C++17 e disabilita le estensioni specifiche del compilatore.
target_compile_features(sensor_simulator PUBLIC cxx_std_17)
target_link_libraries(sensor_simulator PUBLIC environment_simulator)
set_target_properties(sensor_simulator PROPERTIES CXX_EXTENSIONS OFF)

# Adapter sostituibili che isolano il runtime dai simulatori concreti.
add_library(
    io_adapters
    adapters/io_adapters.cpp
)
target_include_directories(io_adapters
    PUBLIC ${PROJECT_SOURCE_DIR}/include
)
target_compile_features(io_adapters PUBLIC cxx_std_17)
target_link_libraries(io_adapters
    PUBLIC
        sensor_simulator
        environment_simulator
        actuator_simulator
)
set_target_properties(io_adapters PROPERTIES CXX_EXTENSIONS OFF)

# Crea la libreria dei controllori.
add_library(
    controllers
    control/controllers.cpp
)
target_include_directories(controllers
    PUBLIC ${PROJECT_SOURCE_DIR}/include
)
target_compile_features(controllers PUBLIC cxx_std_17)
set_target_properties(controllers PROPERTIES CXX_EXTENSIONS OFF)

# Orchestratore delle ricette versionate e delle Strategy confermate.
add_library(
    control_system
    control/control_system.cpp
)
target_include_directories(control_system
    PUBLIC ${PROJECT_SOURCE_DIR}/include
)
target_compile_features(control_system PUBLIC cxx_std_17)
target_link_libraries(control_system
    PUBLIC
        controllers
        environment_simulator
)
set_target_properties(control_system PROPERTIES CXX_EXTENSIONS OFF)

# EventBus Observer e consumer standard per console, CSV e backend.
add_library(
    event_bus
    events/event_bus.cpp
)
target_include_directories(event_bus
    PUBLIC ${PROJECT_SOURCE_DIR}/include
)
target_compile_features(event_bus PUBLIC cxx_std_17)
target_link_libraries(event_bus
    PUBLIC
        control_system
        io_adapters
)
set_target_properties(event_bus PROPERTIES CXX_EXTENSIONS OFF)

# Serializzazione JSON separata dal dominio per non esporre la dipendenza.
add_library(
    recipe_json
    recipes/recipe_json.cpp
)
target_include_directories(recipe_json
    PUBLIC ${PROJECT_SOURCE_DIR}/include
)
target_compile_features(recipe_json PUBLIC cxx_std_17)
target_link_libraries(recipe_json
    PUBLIC
        control_system
    PRIVATE
        nlohmann_json::nlohmann_json
)
set_target_properties(recipe_json PROPERTIES CXX_EXTENSIONS OFF)

# Ciclo operativo che collega ricetta, sensori, attuatori e ambiente.
add_library(
    edge_runtime
    runtime/edge_runtime_core.cpp
    runtime/edge_runtime_configuration.cpp
    runtime/edge_runtime_cycle.cpp
    runtime/edge_runtime_fsm.cpp
    runtime/edge_runtime_actuation.cpp
    runtime/edge_runtime_events.cpp
)
target_include_directories(edge_runtime
    PUBLIC ${PROJECT_SOURCE_DIR}/include
)
target_compile_features(edge_runtime PUBLIC cxx_std_17)
target_link_libraries(edge_runtime
    PUBLIC
        control_system
        io_adapters
        event_bus
)
set_target_properties(edge_runtime PROPERTIES CXX_EXTENSIONS OFF)

# Ricevitore idempotente dei comandi operativi diretti al runtime.
add_library(
    runtime_commands
    runtime/runtime_commands.cpp
)
target_include_directories(runtime_commands
    PUBLIC ${PROJECT_SOURCE_DIR}/include
)
target_compile_features(runtime_commands PUBLIC cxx_std_17)
target_link_libraries(runtime_commands
    PUBLIC
        edge_runtime
)
set_target_properties(runtime_commands PROPERTIES CXX_EXTENSIONS OFF)

# Client asincrono Edge-to-Backend con outbox persistente e libcurl.
add_library(
    http_backend_client
    backend/http_backend_client.cpp
)
target_include_directories(http_backend_client
    PUBLIC ${PROJECT_SOURCE_DIR}/include
)
target_compile_features(http_backend_client PUBLIC cxx_std_17)
target_link_libraries(http_backend_client
    PUBLIC
        event_bus
        runtime_commands
        recipe_json
    PRIVATE
        CURL::libcurl
        Threads::Threads
        nlohmann_json::nlohmann_json
)
set_target_properties(http_backend_client PROPERTIES CXX_EXTENSIONS OFF)

# Coordinamento multi-zona senza condivisione dello stato operativo.
add_library(
    greenhouse_manager
    runtime/greenhouse_manager.cpp
)
target_include_directories(greenhouse_manager
    PUBLIC ${PROJECT_SOURCE_DIR}/include
)
target_compile_features(greenhouse_manager PUBLIC cxx_std_17)
target_link_libraries(greenhouse_manager
    PUBLIC
        runtime_commands
        event_bus
)
set_target_properties(greenhouse_manager PROPERTIES CXX_EXTENSIONS OFF)

# NOME DELL'ESEGUIBILE PRINCIPALE:
# "edge" e sia il nome del target CMake sia, per impostazione
# predefinita, il nome del file generato in build/bin. Per cambiarlo occorre
# sostituire edge anche nei comandi target_* sottostanti.
add_executable(edge main.cpp)
target_compile_features(edge PRIVATE cxx_std_17)
# Collega all'eseguibile le librerie necessarie.
target_link_libraries(edge
    PRIVATE
        greenhouse_manager
        http_backend_client
        recipe_json
)
set_target_properties(edge PROPERTIES CXX_EXTENSIONS OFF)

# Se si vuole cambiare solo il nome del file, lasciando invariato il target:
# set_target_properties(edge PROPERTIES OUTPUT_NAME "nuovo_nome")
