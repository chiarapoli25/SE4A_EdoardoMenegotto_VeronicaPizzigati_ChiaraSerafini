# Versione minima di CMake necessaria per configurare il progetto.
cmake_minimum_required(VERSION 3.16)

# Definisce il progetto, la sua versione e il linguaggio utilizzato.
project(SmartHydroEdge VERSION 0.1.0 LANGUAGES CXX)

# Colloca tutti gli eseguibili, inclusi quelli di test, nella cartella build/bin.
set(CMAKE_RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/bin")

# Legge src/CMakeLists.txt, che definisce librerie ed eseguibile principale.
add_subdirectory(src)

# Permette di escludere le demo interattive nelle build minimali.
option(BUILD_EXPERIMENTS "Build interactive demonstration programs" ON)

if(BUILD_EXPERIMENTS)
    # Questi programmi dimostrativi non vengono registrati come test CTest.
    add_subdirectory(experiments)
endif()

# Abilita il supporto di CTest e crea l'opzione BUILD_TESTING.
include(CTest)

# I test vengono configurati solo se BUILD_TESTING e attivo (valore predefinito: ON).
if(BUILD_TESTING)
    # Legge tests/CMakeLists.txt.
    add_subdirectory(tests)
endif()
