if(NOT DEFINED EXPERIMENT_EXECUTABLE OR NOT DEFINED OUTPUT_DIRECTORY OR
   NOT DEFINED DURATION_CHOICE OR NOT DEFINED SOIL_CHOICE OR
   NOT DEFINED EXPECTED_SAMPLES)
    message(FATAL_ERROR "sensor simulation smoke test is missing variables")
endif()

file(REMOVE_RECURSE "${OUTPUT_DIRECTORY}")
file(MAKE_DIRECTORY "${OUTPUT_DIRECTORY}")
set(input_path "${OUTPUT_DIRECTORY}/input.txt")
# Durata, terriccio, seed e invio finale per chiudere il grafico.
file(WRITE "${input_path}" "${DURATION_CHOICE}\n${SOIL_CHOICE}\n31415\n\n")

# Il terminale testuale verifica la sessione senza aprire una finestra grafica.
execute_process(
    COMMAND ${CMAKE_COMMAND} -E env GNUTERM=dumb "${EXPERIMENT_EXECUTABLE}"
    WORKING_DIRECTORY "${OUTPUT_DIRECTORY}"
    INPUT_FILE "${input_path}"
    RESULT_VARIABLE result
    OUTPUT_VARIABLE output
    ERROR_VARIABLE error
)
if(NOT result EQUAL 0)
    message(FATAL_ERROR "sensor simulation experiment failed:\n${output}\n${error}")
endif()

foreach(expected_text
    "Campioni simulati: ${EXPECTED_SAMPLES}"
    "Attuatori: spenti"
    "CSV salvato in:"
    "Nessun PNG e stato creato."
    "Simulazione terminata.")
    string(FIND "${output}" "${expected_text}" position)
    if(position EQUAL -1)
        message(FATAL_ERROR
            "missing expected text '${expected_text}':\n${output}\n${error}")
    endif()
endforeach()

foreach(forbidden_text
    "unexpected or unrecognized"
    "Reading from '-' inside a multiplot")
    string(FIND "${error}" "${forbidden_text}" position)
    if(NOT position EQUAL -1)
        message(FATAL_ERROR
            "gnuplot reported '${forbidden_text}':\n${output}\n${error}")
    endif()
endforeach()

file(GLOB csv_files "${OUTPUT_DIRECTORY}/experiment_results/*.csv")
list(LENGTH csv_files csv_count)
if(NOT csv_count EQUAL 1)
    message(FATAL_ERROR "the experiment must create exactly one CSV")
endif()
list(GET csv_files 0 csv_path)
file(STRINGS "${csv_path}" csv_lines)
list(LENGTH csv_lines line_count)
math(EXPR expected_lines "${EXPECTED_SAMPLES} + 1")
if(NOT line_count EQUAL expected_lines)
    message(FATAL_ERROR
        "CSV has ${line_count} lines, expected ${expected_lines}")
endif()
file(GLOB png_files "${OUTPUT_DIRECTORY}/experiment_results/*.png")
if(png_files)
    message(FATAL_ERROR "the live sensor experiment must not create PNG files")
endif()
