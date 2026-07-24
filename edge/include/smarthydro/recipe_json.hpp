#pragma once

/**
 * @file recipe_json.hpp
 * @brief Serializzazione JSON delle ricette e configurazioni Strategy.
 */

#include "smarthydro/control_system.hpp"

#include <string>

namespace smarthydro {

/** @brief Serializza una ricetta in JSON leggibile. */
std::string recipe_to_json(const Recipe& recipe);

/**
 * @brief Deserializza e valida una ricetta JSON.
 * @throws std::invalid_argument Se JSON o ricetta non sono validi.
 */
Recipe recipe_from_json(const std::string& json_text);

/** @brief Carica e valida una ricetta da file. */
Recipe load_recipe_json(const std::string& path);

/** @brief Salva una ricetta in un file JSON. */
void save_recipe_json(const Recipe& recipe, const std::string& path);

}  // namespace smarthydro
