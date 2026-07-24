if(NOT DEFINED EXPERIMENT_EXECUTABLE OR NOT DEFINED OUTPUT_DIRECTORY)
    message(FATAL_ERROR "recipe control smoke test is missing variables")
endif()

file(REMOVE_RECURSE "${OUTPUT_DIRECTORY}")
file(MAKE_DIRECTORY "${OUTPUT_DIRECTORY}")
set(input_path "${OUTPUT_DIRECTORY}/close_plot.txt")
file(WRITE "${input_path}" "\n")

execute_process(
    COMMAND
        "${CMAKE_COMMAND}" -E env "GNUTERM=dumb"
        "${EXPERIMENT_EXECUTABLE}" "${OUTPUT_DIRECTORY}/results"
    WORKING_DIRECTORY "${OUTPUT_DIRECTORY}"
    INPUT_FILE "${input_path}"
    RESULT_VARIABLE result
    OUTPUT_VARIABLE output
    ERROR_VARIABLE error
)
if(NOT result EQUAL 0)
    message(FATAL_ERROR "recipe control experiment failed:\n${output}\n${error}")
endif()

foreach(expected_text
    "Fase simulata: VegetativeGrowth"
    "Nessun PNG e stato creato."
    "Experiment terminato.")
    string(FIND "${output}" "${expected_text}" position)
    if(position EQUAL -1)
        message(FATAL_ERROR
            "missing expected text '${expected_text}':\n${output}\n${error}")
    endif()
endforeach()

set(csv_path "${OUTPUT_DIRECTORY}/results/recipe_phase_simulation.csv")
if(NOT EXISTS "${csv_path}")
    message(FATAL_ERROR "the recipe experiment did not create its CSV")
endif()
file(STRINGS "${csv_path}" csv_lines)
list(LENGTH csv_lines line_count)
if(NOT line_count EQUAL 1346)
    message(FATAL_ERROR "recipe CSV has ${line_count} lines, expected 1346")
endif()

file(GLOB_RECURSE png_files "${OUTPUT_DIRECTORY}/*.png")
if(png_files)
    message(FATAL_ERROR
        "the live recipe experiment must not create PNG files: ${png_files}")
endif()
