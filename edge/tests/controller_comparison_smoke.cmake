if(NOT DEFINED EXPERIMENT_EXECUTABLE OR NOT DEFINED OUTPUT_DIRECTORY)
    message(FATAL_ERROR "controller comparison smoke test is missing variables")
endif()

file(REMOVE_RECURSE "${OUTPUT_DIRECTORY}")
file(MAKE_DIRECTORY "${OUTPUT_DIRECTORY}")
set(input_path "${OUTPUT_DIRECTORY}/close_plot.txt")
file(WRITE "${input_path}" "\n")

execute_process(
    COMMAND
        "${CMAKE_COMMAND}" -E env "GNUTERM=dumb"
        "${EXPERIMENT_EXECUTABLE}"
    WORKING_DIRECTORY "${OUTPUT_DIRECTORY}"
    INPUT_FILE "${input_path}"
    RESULT_VARIABLE result
    OUTPUT_VARIABLE output
    ERROR_VARIABLE error
)
if(NOT result EQUAL 0)
    message(FATAL_ERROR "experiment failed:\n${output}\n${error}")
endif()

if(NOT output MATCHES "Experiment terminato")
    message(FATAL_ERROR "experiment did not complete:\n${output}\n${error}")
endif()

file(GLOB generated_files
    "${OUTPUT_DIRECTORY}/*.csv"
    "${OUTPUT_DIRECTORY}/*.png"
    "${OUTPUT_DIRECTORY}/experiment_results/*")
if(generated_files)
    message(FATAL_ERROR
        "controller comparison created persistent output: ${generated_files}")
endif()
