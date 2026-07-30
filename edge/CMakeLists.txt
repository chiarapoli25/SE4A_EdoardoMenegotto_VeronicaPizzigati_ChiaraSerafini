# Versione minima di CMake necessaria per configurare il progetto.
cmake_minimum_required(VERSION 3.16)

# Definisce il progetto, la sua versione e il linguaggio utilizzato.
project(SmartHydroEdge VERSION 0.1.0 LANGUAGES CXX)

find_package(CURL REQUIRED)
find_package(Threads REQUIRED)

# Usa nlohmann/json installato quando disponibile, con fallback riproducibile.
find_package(nlohmann_json 3.11 QUIET)
if(NOT nlohmann_json_FOUND)
    include(FetchContent)
    FetchContent_Declare(
        nlohmann_json
        URL https://github.com/nlohmann/json/releases/download/v3.11.3/json.tar.xz
        DOWNLOAD_EXTRACT_TIMESTAMP TRUE
    )
    FetchContent_MakeAvailable(nlohmann_json)
endif()

# Colloca tutti gli eseguibili, inclusi quelli di test, nella cartella build/bin.
set(CMAKE_RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/bin")

# Abilita il supporto di CTest e crea l'opzione BUILD_TESTING prima degli
# experiments, cosi anche le demo non interattive possono avere smoke test.
include(CTest)

# Legge src/CMakeLists.txt, che definisce librerie ed eseguibile principale.
add_subdirectory(src)

# Permette di escludere le demo interattive nelle build minimali.
option(BUILD_EXPERIMENTS "Build interactive demonstration programs" ON)

if(BUILD_EXPERIMENTS)
    # Legge le demo interattive e gli experiment riproducibili.
    add_subdirectory(experiments)
endif()

# I test vengono configurati solo se BUILD_TESTING e attivo (valore predefinito: ON).
if(BUILD_TESTING)
    # Legge tests/CMakeLists.txt.
    add_subdirectory(tests)
endif()
