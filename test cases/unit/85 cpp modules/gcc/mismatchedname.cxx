export module matchedname;     // primary module interface unit
 
export import :A;    // Hello() is visible when importing 'matchedname'.
import :B;           // WorldImpl() is now visible only for 'matchedname'.
// export import :B; // ERROR: Cannot export a module implementation unit.
 
// World() is visible by any translation unit importing 'matchedname'.
export char const* World()
{
    return WorldImpl();
}

