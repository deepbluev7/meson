export module matchedname:A; // partition module interface unit
 
// Hello() is visible by any translation unit importing 'matchedname'.
export char const* Hello() { return "Hello"; }
