# Unico experiment interattivo per pompa, elettrovalvole e illuminazione.
# Per cambiare il nome dell'eseguibile, modificare il primo argomento.
add_executable(actuator_simulation actuator_simulation.cpp)
target_link_libraries(actuator_simulation PRIVATE actuator_simulator)
target_compile_features(actuator_simulation PRIVATE cxx_std_17)
set_target_properties(actuator_simulation PROPERTIES CXX_EXTENSIONS OFF)
