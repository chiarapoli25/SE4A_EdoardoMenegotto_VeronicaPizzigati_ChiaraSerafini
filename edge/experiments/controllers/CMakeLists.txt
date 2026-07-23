# Aggiunge un experiment standard e lo collega ai controllori e all'output comune.
function(add_controller_experiment target_name source_file)
    add_executable(${target_name} ${source_file})
    target_link_libraries(${target_name} PRIVATE controllers experiment_output)
    target_compile_features(${target_name} PRIVATE cxx_std_17)
    set_target_properties(${target_name} PROPERTIES CXX_EXTENSIONS OFF)
endfunction()

add_controller_experiment(threshold_step_response threshold_step_response.cpp)
add_controller_experiment(pid_step_response pid_step_response.cpp)
add_controller_experiment(predictive_step_response predictive_step_response.cpp)
